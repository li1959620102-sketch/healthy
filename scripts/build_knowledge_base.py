# -*- coding: utf-8 -*-
"""构建平台专属知识库：knowledge_base/*.md → 分块 → bge-m3 嵌入 → Milvus 集合。

用法：
    D:/conda/envs/ai_py311/python.exe scripts/build_knowledge_base.py

说明：
- 集合名取 settings.MILVUS_DEFAULT_COLLECTION（.env 配置），默认 ai_health_kb_v1；
- drop_old=True：重复执行会先删旧集合同名重建（幂等，换文档后重跑即可）；
- 分块策略：优先按 Markdown 标题切，再按长度 400 / 重叠 60；
- 元数据：file_name（引用徽标显示名）、source（文档标题）、category。
"""
import os
import sys

# 本机 Ollama (localhost:11434) 不走任何代理——沙箱/系统代理会把 localhost 请求劫持到代理端口
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings

KB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge_base")


def load_documents():
    from langchain_core.documents import Document
    docs = []
    for name in sorted(os.listdir(KB_DIR)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(KB_DIR, name)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if not text:
            continue
        # 文档标题 = 第一个 # 标题；类别 = 文件名前缀（去掉序号与扩展名）
        first_line = text.splitlines()[0].lstrip("# ").strip()
        category = name[:-3].split("_", 1)[-1] if "_" in name else name[:-3]
        docs.append(Document(page_content=text, metadata={
            "file_name": name,
            "source": first_line or name,
            "category": category,
        }))
    return docs


def main():
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_ollama import OllamaEmbeddings
    from langchain_milvus import Milvus
    import pymilvus

    raw = load_documents()
    if not raw:
        print("knowledge_base/ 下没有可用的 .md 文档")
        return
    print(f"载入 {len(raw)} 个文档")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=400, chunk_overlap=60,
        separators=["\n## ", "\n### ", "\n\n", "\n", "。", "；", "，", " ", ""],
        length_function=len,
    )
    chunks = splitter.split_documents(raw)
    for c in chunks:
        c.metadata["file_name"] = c.metadata.get("file_name", "")
        c.metadata["source"] = c.metadata.get("source", "")
    print(f"分块完成：{len(chunks)} 块")

    emb = OllamaEmbeddings(model=settings.EMBEDDING_MODEL, base_url=settings.OLLAMA_BASE_URL)
    pymilvus.connections.connect(
        alias=settings.MILVUS_DEFAULT_ALIAS, host=settings.MILVUS_HOST,
        port=settings.MILVUS_PORT, db_name=settings.MILVUS_DEFAULT_DB,
    )
    vs = Milvus(
        embedding_function=emb,
        collection_name=settings.MILVUS_DEFAULT_COLLECTION,
        connection_args={"host": settings.MILVUS_HOST, "port": settings.MILVUS_PORT,
                         "alias": settings.MILVUS_DEFAULT_ALIAS},
        auto_id=True, drop_old=True,
        index_params={"index_type": "IVF_FLAT", "metric_type": "COSINE"},
        search_params={"metric_type": "COSINE", "params": {"nprobe": 16}},
    )
    # 分批入库：一次性把上千块塞给 Ollama 会把嵌入 runner 压垮（连接拒绝），
    # 每批 32 块、失败重试一次。
    BATCH = 32
    total = 0
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i:i + BATCH]
        for attempt in (1, 2):
            try:
                vs.add_documents(batch)
                total += len(batch)
                print(f"进度 {total}/{len(chunks)}", flush=True)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    raise
                print(f"第 {i // BATCH + 1} 批失败，重试：{str(e)[:120]}", flush=True)
    ids = list(range(total))
    print(f"已写入集合 {settings.MILVUS_DEFAULT_COLLECTION}：{total} 条向量")
    stats = vs.col.num_entities
    print(f"集合实体数：{stats}")


if __name__ == "__main__":
    main()
