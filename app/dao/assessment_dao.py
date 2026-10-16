# -*- coding: utf-8 -*-
"""D/E/F 组数据访问层：测评模板、测评记录、测评报告、营养师批注。

约定：
- 所有方法均接受可选 `session` 参数：传入则由调用方管理事务（跨表操作必须传），
  不传则方法内自建会话并即时关闭。
- `session_scope()` 供 service 层做跨表事务使用。
"""
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import SQLAlchemyError

from app.models.mysql.platform import (
    AssessmentRecord,
    AssessmentReport,
    AssessmentTemplate,
    ReportAnnotation,
)
from app.utils.db import get_session

logger = logging.getLogger(__name__)


@contextmanager
def session_scope():
    """跨表事务上下文：正常提交，异常回滚，最终关闭会话。"""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class TemplateDAO:
    """测评模板（版本不可变：编辑即生成新版本行）。"""

    @staticmethod
    def create(data: Dict[str, Any], session=None) -> AssessmentTemplate:
        own = session is None
        session = session or get_session()
        try:
            tpl = AssessmentTemplate(**data)
            session.add(tpl)
            if own:
                session.commit()
                session.refresh(tpl)
            else:
                session.flush()
            return tpl
        except SQLAlchemyError:
            if own:
                session.rollback()
            raise
        finally:
            if own:
                session.close()

    @staticmethod
    def get(template_id: int, session=None) -> Optional[AssessmentTemplate]:
        own = session is None
        session = session or get_session()
        try:
            return session.query(AssessmentTemplate).filter_by(id=template_id).first()
        finally:
            if own:
                session.close()

    @staticmethod
    def get_published(template_id: int, session=None) -> Optional[AssessmentTemplate]:
        """仅返回已发布模板（用户侧发起测评用）。"""
        own = session is None
        session = session or get_session()
        try:
            return (session.query(AssessmentTemplate)
                    .filter_by(id=template_id, status="published").first())
        finally:
            if own:
                session.close()

    @staticmethod
    def list_published(type_: Optional[str] = None, session=None) -> List[AssessmentTemplate]:
        own = session is None
        session = session or get_session()
        try:
            q = session.query(AssessmentTemplate).filter_by(status="published")
            if type_:
                q = q.filter_by(type=type_)
            return q.order_by(AssessmentTemplate.published_at.desc()).all()
        finally:
            if own:
                session.close()

    @staticmethod
    def list_mine(created_by: str, session=None) -> List[AssessmentTemplate]:
        own = session is None
        session = session or get_session()
        try:
            return (session.query(AssessmentTemplate)
                    .filter_by(created_by=created_by)
                    .order_by(AssessmentTemplate.id.desc()).all())
        finally:
            if own:
                session.close()

    @staticmethod
    def list_versions(code: str, session=None) -> List[AssessmentTemplate]:
        own = session is None
        session = session or get_session()
        try:
            return (session.query(AssessmentTemplate)
                    .filter_by(code=code)
                    .order_by(AssessmentTemplate.version.desc()).all())
        finally:
            if own:
                session.close()

    @staticmethod
    def latest_version(code: str, session=None) -> int:
        own = session is None
        session = session or get_session()
        try:
            row = (session.query(AssessmentTemplate)
                   .filter_by(code=code)
                   .order_by(AssessmentTemplate.version.desc()).first())
            return int(row.version) if row else 0
        finally:
            if own:
                session.close()

    @staticmethod
    def new_version(tpl: AssessmentTemplate, updates: Dict[str, Any], session=None) -> AssessmentTemplate:
        """基于现有模板生成新版本行（原行保持不变，保证历史报告可追溯）。"""
        own = session is None
        session = session or get_session()
        try:
            base = {
                "code": tpl.code,
                "version": int(tpl.version) + 1,
                "name": tpl.name,
                "type": tpl.type,
                "description": tpl.description,
                "dimension_config": tpl.dimension_config,
                "question_config": tpl.question_config,
                "status": "draft",
                "created_by": tpl.created_by,
            }
            base.update({k: v for k, v in (updates or {}).items()
                         if k in ("name", "type", "description",
                                  "dimension_config", "question_config")})
            new_tpl = AssessmentTemplate(**base)
            session.add(new_tpl)
            if own:
                session.commit()
                session.refresh(new_tpl)
            else:
                session.flush()
            return new_tpl
        except SQLAlchemyError:
            if own:
                session.rollback()
            raise
        finally:
            if own:
                session.close()

    @staticmethod
    def submit_audit(template_id: int, session=None) -> Optional[AssessmentTemplate]:
        return TemplateDAO._set_status(template_id, "pending", submitted_at=datetime.now(), session=session)

    @staticmethod
    def audit(template_id: int, auditor_id: str, approve: bool,
              remark: str = "", session=None) -> Optional[AssessmentTemplate]:
        status = "published" if approve else "draft"
        extra: Dict[str, Any] = {
            "audit_at": datetime.now(),
            "auditor_id": auditor_id,
            "audit_remark": remark or ("审核通过" if approve else "审核驳回"),
        }
        if approve:
            extra["published_at"] = datetime.now()
        return TemplateDAO._set_status(template_id, status, session=session, **extra)

    @staticmethod
    def offline(template_id: int, session=None) -> Optional[AssessmentTemplate]:
        return TemplateDAO._set_status(template_id, "offline", offline_at=datetime.now(), session=session)

    @staticmethod
    def _set_status(template_id: int, status: str, session=None, **extra) -> Optional[AssessmentTemplate]:
        own = session is None
        session = session or get_session()
        try:
            tpl = session.query(AssessmentTemplate).filter_by(id=template_id).first()
            if not tpl:
                return None
            tpl.status = status
            for k, v in extra.items():
                setattr(tpl, k, v)
            if own:
                session.commit()
                session.refresh(tpl)
            return tpl
        except SQLAlchemyError:
            if own:
                session.rollback()
            raise
        finally:
            if own:
                session.close()


class RecordDAO:
    """测评记录（含草稿、游客）。"""

    @staticmethod
    def create(data: Dict[str, Any], session=None) -> AssessmentRecord:
        own = session is None
        session = session or get_session()
        try:
            rec = AssessmentRecord(**data)
            session.add(rec)
            if own:
                session.commit()
                session.refresh(rec)
            else:
                session.flush()
            return rec
        except SQLAlchemyError:
            if own:
                session.rollback()
            raise
        finally:
            if own:
                session.close()

    @staticmethod
    def get(record_id: int, session=None) -> Optional[AssessmentRecord]:
        own = session is None
        session = session or get_session()
        try:
            return session.query(AssessmentRecord).filter_by(id=record_id).first()
        finally:
            if own:
                session.close()

    @staticmethod
    def get_for_update(record_id: int, session=None) -> Optional[AssessmentRecord]:
        """带行锁读取测评记录（SELECT ... FOR UPDATE）。

        提交接口用它串行化同一测评的并发提交：先拿到锁的请求写报告并提交事务，
        后到的请求拿到锁时已能看到 completed 状态，从而走幂等分支返回同一份报告，
        避免并发 INSERT 撞 assessment_report.uk_record 唯一键（BV-29）。
        """
        own = session is None
        session = session or get_session()
        try:
            return (session.query(AssessmentRecord)
                    .filter_by(id=record_id)
                    .populate_existing()
                    .with_for_update()
                    .first())
        finally:
            if own:
                session.close()

    @staticmethod
    def get_by_idempotency(key: Optional[str], session=None) -> Optional[AssessmentRecord]:
        if not key:
            return None
        own = session is None
        session = session or get_session()
        try:
            return session.query(AssessmentRecord).filter_by(idempotency_key=key).first()
        finally:
            if own:
                session.close()

    @staticmethod
    def list_by_user(user_id: str, status: Optional[str] = None,
                     limit: int = 50, session=None) -> List[AssessmentRecord]:
        own = session is None
        session = session or get_session()
        try:
            q = session.query(AssessmentRecord).filter_by(user_id=user_id)
            if status:
                q = q.filter_by(status=status)
            return q.order_by(AssessmentRecord.id.desc()).limit(limit).all()
        finally:
            if own:
                session.close()

    @staticmethod
    def save_draft(record_id: int, answers: Any, current_page: Optional[int] = None,
                   progress: Optional[str] = None, expect_saved_at: Optional[datetime] = None,
                   session=None) -> Optional[AssessmentRecord]:
        """保存草稿。传 expect_saved_at 时启用乐观式并发控制（不匹配返回 None）。"""
        own = session is None
        session = session or get_session()
        try:
            q = session.query(AssessmentRecord).filter_by(id=record_id, status="in_progress")
            if expect_saved_at is not None:
                q = q.filter(AssessmentRecord.last_saved_at == expect_saved_at)
            rec = q.first()
            if not rec:
                return None
            rec.answers = answers
            if current_page is not None:
                rec.current_page = current_page
            if progress is not None:
                rec.progress = progress
            # 秒级截断再落库：`last_saved_at` 列是 DATETIME（无小数位），MySQL 会把
            # 微秒四舍五入，而内存对象仍带微秒 ⇒ 读出来的 version 与库里的值对不上，
            # 多端并发保护会永远误判"草稿已被其他端更新"（IT-AS-18 实测踩到）。
            rec.last_saved_at = datetime.now().replace(microsecond=0)
            if own:
                session.commit()
                session.refresh(rec)
            return rec
        except SQLAlchemyError:
            if own:
                session.rollback()
            raise
        finally:
            if own:
                session.close()

    @staticmethod
    def mark_submitted(record_id: int, idempotency_key: Optional[str],
                       session=None) -> Optional[AssessmentRecord]:
        own = session is None
        session = session or get_session()
        try:
            rec = session.query(AssessmentRecord).filter_by(id=record_id).first()
            if not rec:
                return None
            rec.status = "completed"
            rec.submitted_at = datetime.now()
            if idempotency_key:
                rec.idempotency_key = idempotency_key
            if own:
                session.commit()
                session.refresh(rec)
            return rec
        except SQLAlchemyError:
            if own:
                session.rollback()
            raise
        finally:
            if own:
                session.close()

    @staticmethod
    def void(record_id: int, session=None) -> Optional[AssessmentRecord]:
        own = session is None
        session = session or get_session()
        try:
            rec = session.query(AssessmentRecord).filter_by(id=record_id).first()
            if not rec:
                return None
            rec.status = "void"
            rec.voided_at = datetime.now()
            if own:
                session.commit()
                session.refresh(rec)
            return rec
        except SQLAlchemyError:
            if own:
                session.rollback()
            raise
        finally:
            if own:
                session.close()

    @staticmethod
    def list_guest(guest_hash: str, days: int = 7, session=None) -> List[AssessmentRecord]:
        """游客记录：仅认领 N 天内、未绑定账号、未完成的也一并认领（M3）。"""
        own = session is None
        session = session or get_session()
        try:
            since = datetime.now() - timedelta(days=days)
            return (session.query(AssessmentRecord)
                    .filter(AssessmentRecord.guest_token == guest_hash,
                            AssessmentRecord.user_id.is_(None),
                            AssessmentRecord.started_at >= since)
                    .all())
        finally:
            if own:
                session.close()

    @staticmethod
    def count_guest_completed(guest_hash: str, session=None) -> int:
        """游客已完成的测评数（BV-21 配额用）。"""
        own = session is None
        session = session or get_session()
        try:
            return (session.query(AssessmentRecord)
                    .filter(AssessmentRecord.guest_token == guest_hash,
                            AssessmentRecord.status == "completed")
                    .count())
        finally:
            if own:
                session.close()

    @staticmethod
    def find_in_progress(template_id: int, user_id: Optional[str] = None,
                         guest_hash: Optional[str] = None, session=None) -> Optional[AssessmentRecord]:
        """同一模板下尚未提交的测评（IT-AS-13 用：再次发起时引导继续作答）。

        只认 in_progress：已提交/已作废的都不算"未完成"，
        否则用户做完一次后就再也发不了新测评。
        """
        own = session is None
        session = session or get_session()
        try:
            q = session.query(AssessmentRecord).filter(
                AssessmentRecord.template_id == template_id,
                AssessmentRecord.status == "in_progress",
            )
            if user_id:
                q = q.filter(AssessmentRecord.user_id == str(user_id))
            elif guest_hash:
                q = q.filter(AssessmentRecord.guest_token == guest_hash,
                             AssessmentRecord.user_id.is_(None))
            else:
                return None
            return q.order_by(AssessmentRecord.id.desc()).first()
        finally:
            if own:
                session.close()

    @staticmethod
    def claim(guest_hash: str, user_id: str, days: int = 7, session=None) -> int:
        """把游客记录挂到正式账号下，返回认领条数。"""
        rows = RecordDAO.list_guest(guest_hash, days=days, session=session)
        own = session is None
        s = session or get_session()
        try:
            count = 0
            for r in rows:
                r.user_id = user_id
                count += 1
            if own:
                s.commit()
            return count
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own and session is None:
                s.close()


class ReportDAO:
    """测评报告（评分结构化存储）。"""

    @staticmethod
    def create(data: Dict[str, Any], session=None) -> AssessmentReport:
        own = session is None
        session = session or get_session()
        try:
            rep = AssessmentReport(**data)
            session.add(rep)
            if own:
                session.commit()
                session.refresh(rep)
            else:
                session.flush()
            return rep
        except SQLAlchemyError:
            if own:
                session.rollback()
            raise
        finally:
            if own:
                session.close()

    @staticmethod
    def get(report_id: int, session=None) -> Optional[AssessmentReport]:
        own = session is None
        session = session or get_session()
        try:
            return session.query(AssessmentReport).filter_by(id=report_id).first()
        finally:
            if own:
                session.close()

    @staticmethod
    def get_by_record(record_id: int, session=None) -> Optional[AssessmentReport]:
        own = session is None
        session = session or get_session()
        try:
            return session.query(AssessmentReport).filter_by(record_id=record_id).first()
        finally:
            if own:
                session.close()

    @staticmethod
    def mark_complete(report_id: int, analysis_text: str, references: Any,
                      elapsed_ms: int, model_name: Optional[str] = None,
                      session=None) -> Optional[AssessmentReport]:
        own = session is None
        session = session or get_session()
        try:
            rep = session.query(AssessmentReport).filter_by(id=report_id).first()
            if not rep:
                return None
            rep.state = "complete"
            rep.analysis_text = analysis_text
            rep.references = references
            rep.elapsed_ms = elapsed_ms
            if model_name:
                rep.model_name = model_name
            if own:
                session.commit()
                session.refresh(rep)
            return rep
        except SQLAlchemyError:
            if own:
                session.rollback()
            raise
        finally:
            if own:
                session.close()

    @staticmethod
    def mark_basic(report_id: int, retry_count: int = 0,
                   partial_text: Optional[str] = None, references: Any = None,
                   session=None) -> Optional[AssessmentReport]:
        """降级为基础版：评分仍在，分析文本可能缺失或只有片段，可重试。

        references 一并落库：降级丢的是「模型写的分析文本」，**不丢来源引用**。
        检索在生成之前就已完成，把已命中的知识库片段留住，
        用户至少还能顺着来源自查（用例 IT-R-03 要求引用可溯源）。
        """
        own = session is None
        session = session or get_session()
        try:
            rep = session.query(AssessmentReport).filter_by(id=report_id).first()
            if not rep:
                return None
            rep.state = "basic"
            rep.retry_count = retry_count
            # 断点续传：保留已生成的片段，避免用户白等（需求 A8）
            rep.analysis_text = partial_text
            if references:
                rep.references = references
            if own:
                session.commit()
                session.refresh(rep)
            return rep
        except SQLAlchemyError:
            if own:
                session.rollback()
            raise
        finally:
            if own:
                session.close()

    @staticmethod
    def list_by_user(user_id: str, limit: int = 50, session=None) -> List[AssessmentReport]:
        own = session is None
        session = session or get_session()
        try:
            return (session.query(AssessmentReport)
                    .filter_by(user_id=user_id)
                    .order_by(AssessmentReport.generated_at.desc()).limit(limit).all())
        finally:
            if own:
                session.close()


class AnnotationDAO:
    """营养师批注（与智能体原文形成双版本）。"""

    @staticmethod
    def upsert(report_id: int, dietitian_id: str, content: str,
               session=None) -> ReportAnnotation:
        own = session is None
        session = session or get_session()
        try:
            ann = (session.query(ReportAnnotation)
                   .filter_by(report_id=report_id, dietitian_id=dietitian_id).first())
            if ann:
                ann.content = content
            else:
                ann = ReportAnnotation(report_id=report_id, dietitian_id=dietitian_id, content=content)
                session.add(ann)
            if own:
                session.commit()
                session.refresh(ann)
            else:
                session.flush()
            return ann
        except SQLAlchemyError:
            if own:
                session.rollback()
            raise
        finally:
            if own:
                session.close()

    @staticmethod
    def list_by_report(report_id: int, session=None) -> List[ReportAnnotation]:
        own = session is None
        session = session or get_session()
        try:
            return (session.query(ReportAnnotation)
                    .filter_by(report_id=report_id)
                    .order_by(ReportAnnotation.created_at.asc()).all())
        finally:
            if own:
                session.close()
