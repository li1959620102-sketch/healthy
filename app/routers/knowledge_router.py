# -*- coding: utf-8 -*-
"""K 组 · 知识库路由（函数式，依据设计_01 §二 K）。

覆盖范围：文档上传（自动向量化）/ 向量化触发 / 文档列表 / 删除。
复用既有 KnowledgeFileService 与 MinIO 上传链路；向量库为 Milvus（已定）。
注：上传/向量化依赖 MinIO 与 Milvus 在线，离线时接口会返回错误（符合预期）。
"""
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile, Query

from app.services.knowledge_file_service import KnowledgeFileService
from app.utils.file_handler import UploadFile as FileUploader
from app.core.responses import success
from app.core.errors import ErrCode, BizError
from app.core import rules
from app.core.deps import require_role, ROLE_ADMIN
from app.config import settings

router = APIRouter(prefix="/knowledge", tags=["知识库"])

kb_service = KnowledgeFileService()
KB_BUCKET_NAME = settings.KB_BUCKET_NAME
SUPPORTED_EXTENSIONS = {
    'pdf': 'application/pdf', 'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xls': 'application/vnd.ms-excel', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'txt': 'text/plain', 'sql': 'text/sql', 'json': 'application/json', 'md': 'text/markdown',
}


@router.post("/docs", summary="上传知识文档", description="上传指南/食材/期刊类文档并自动向量化。仅管理员。")
async def upload_doc(
    file: UploadFile = File(..., description="文档（pdf/docx/txt/xlsx/md…）"),
    collection_name: str = Form(..., description="Milvus 集合名称（按知识域划分）"),
    category: str = Form("guideline", description="分类 food 食材 / disease 疾病 / guideline 指南"),
    source: str = Form("", description="来源，如 中国居民膳食指南2022"),
    _: dict = Depends(require_role(ROLE_ADMIN)),
):
    if file.filename.split('.')[-1].lower() not in SUPPORTED_EXTENSIONS:
        raise BizError(ErrCode.PARAM_INVALID, "不支持的文件类型")
    if not KB_BUCKET_NAME:
        raise BizError(ErrCode.PARAM_INVALID, "未配置 KB_BUCKET_NAME")

    uploader = FileUploader()
    uploaded = await uploader.upload_file_form(file, KB_BUCKET_NAME)
    if not uploaded:
        raise BizError(ErrCode.SYSTEM_ERROR, "MinIO 上传失败")

    # 库里只存**对象名**，不存预签名 URL：签名 URL 7 天后必然失效，
    # 存进库就成了后台点不开的死链（要访问时调 /docs/{id}/download 现签）。
    stored_name = uploaded.get("stored_filename")
    doc = kb_service.save_knowledge_file({
        "collection_name": collection_name,
        "file_name": stored_name,
        "file_type": file.filename.split('.')[-1].lower(),
        "minio_path": stored_name,
        "chunk_strategy": "adaptive",
        "status": "uploaded",
        "category": category,
        "source": source,
        "embed_status": "pending",
    })
    # 触发向量化（失败不阻断上传，状态回写 failed 便于重试）
    # 状态链 pending → processing → done/failed 全部落在 `embed_status` 上
    # （原实现写的是 `status="processed"`，既非 status 的合法取值、也不反映向量化，
    #  导致该字段永远是 pending；见 KnowledgeFileService.update_embed_status 注释）。
    kb_service.update_embed_status(doc.id, "processing")
    try:
        records = kb_service.process_file_from_minio(
            file_name=doc.file_name, minio_path=doc.minio_path,
            collection_name=collection_name, file_id=doc.id,
            category=category, source=source,
        )
        kb_service.update_embed_status(doc.id, "done", chunk_count=len(records))
    except Exception as e:
        kb_service.update_embed_status(doc.id, "failed", fail_reason=str(e))
        logger = __import__("logging").getLogger("knowledge_router")
        logger.error(f"向量化失败（已标记 failed，可重试）：{e}")
    return success(data=kb_service.get_file_by_id(doc.id).to_dict())


@router.post("/docs/{doc_id}/embed", summary="触发向量化", description="对上传后未向量化/失败文档重跑向量化。仅管理员。")
async def embed_doc(doc_id: str, _: dict = Depends(require_role(ROLE_ADMIN))):
    from app.utils.db import get_session
    from app.models.mysql.knowledge_file import KnowledgeFile
    session = get_session()
    try:
        doc = session.query(KnowledgeFile).filter_by(id=doc_id).first()
        if not doc:
            raise BizError(ErrCode.RESOURCE_NOT_FOUND, "文档不存在")
        # 先把要用的标量取出来，再关 session：session 关掉后 ORM 对象属性会过期，
        # 再去访问就会抛 DetachedInstanceError（这里踩过）。
        (file_name, minio_path, collection_name,
         doc_category, doc_source) = (doc.file_name, doc.minio_path,
                                      doc.collection_name, doc.category, doc.source)
    finally:
        session.close()

    kb_service.update_embed_status(doc_id, "processing")
    try:
        records = kb_service.process_file_from_minio(
            file_name=file_name, minio_path=minio_path,
            collection_name=collection_name, file_id=doc_id,
            category=doc_category, source=doc_source,
        )
    except Exception as e:
        kb_service.update_embed_status(doc_id, "failed", fail_reason=str(e))
        raise BizError(ErrCode.SYSTEM_ERROR, f"向量化失败：{e}")
    kb_service.update_embed_status(doc_id, "done", chunk_count=len(records))
    return success(message=f"向量化完成，写入 {len(records)} 个知识片段")


@router.get("/docs/{doc_id}/download", summary="签发原文件临时下载地址",
            description="按需签发 MinIO 预签名地址（默认 10 分钟有效）。仅管理员。")
async def download_doc(doc_id: str, _: dict = Depends(require_role(ROLE_ADMIN))):
    from app.utils.db import get_session
    from app.models.mysql.knowledge_file import KnowledgeFile
    session = get_session()
    try:
        doc = session.query(KnowledgeFile).filter_by(id=doc_id).first()
        if not doc:
            raise BizError(ErrCode.RESOURCE_NOT_FOUND, "文档不存在")
        file_name = doc.file_name
    finally:
        session.close()
    try:
        url = kb_service.presign_original(file_name)
    except Exception as e:                       # noqa: BLE001
        raise BizError(ErrCode.SYSTEM_ERROR, f"签发下载地址失败：{e}")
    return success(data={"url": url, "expires_in_minutes": 10})


@router.get("/docs", summary="知识文档列表", description="分页返回文档与向量化状态。仅管理员。page<1 归为 1，page_size 上限 100（BV-19）。")
async def list_docs(
    page: int = Query(1, description="页码（<1 归为 1）"),
    page_size: int = Query(10, description=f"每页数量（1–{rules.PAGE_SIZE_MAX}，超出截断）"),
    _: dict = Depends(require_role(ROLE_ADMIN)),
):
    page, page_size = rules.normalize_paging(page, page_size)
    result = kb_service.list_knowledge_files(page=page, per_page=page_size)
    return success(data={
        "items": result.get("items", []),
        "total": result.get("total", 0),
        "page": result.get("page", page),
        "page_size": result.get("per_page", page_size),
        "has_more": (result.get("page", 1) * result.get("per_page", page_size)) < result.get("total", 0),
    })


@router.delete("/docs/{doc_id}", summary="删除文档", description="删除文档记录并**同步清理向量库**中该文档的全部分块（IT-K-03 / 评审 M5）。仅管理员。")
async def delete_doc(doc_id: str, _: dict = Depends(require_role(ROLE_ADMIN))):
    from app.utils.db import get_session
    from app.models.mysql.knowledge_file import KnowledgeFile
    session = get_session()
    try:
        doc = session.query(KnowledgeFile).filter_by(id=doc_id).first()
        if not doc:
            raise BizError(ErrCode.RESOURCE_NOT_FOUND, "文档不存在")
        (file_name, collection_name) = (doc.file_name, doc.collection_name)

        # 顺序很重要：**先删向量，成功了再删元数据**。
        # 反过来（先删元数据）一旦向量清理失败，就留下"元数据没了、向量还在"的状态：
        # 库里再也找不到这条文档，运维却不知道该去删哪些向量——
        # AI 会继续引用一份"已删除"的材料，引用里的 doc_id 又指向不存在的记录，
        # 来源彻底不可溯源（A7-4 红线）。
        # 向量清理失败就整体失败、保留记录，管理员可重试；宁可"没删掉"，不能"删一半"。
        try:
            removed = kb_service.delete_vectors(collection_name, file_name, file_id=doc.id)
        except Exception as e:
            raise BizError(ErrCode.SYSTEM_ERROR, f"向量清理失败，已保留文档记录以便重试：{e}")

        session.delete(doc)
        session.commit()
        return success(message=f"文档及其 {removed} 个知识片段已删除")
    except BizError:
        raise
    except Exception as e:
        session.rollback()
        raise BizError(ErrCode.DB_ERROR, f"删除失败：{e}")
    finally:
        session.close()
