# -*- coding: utf-8 -*-
"""J/P 组数据访问层：用户管理、系统配置、操作日志、运营统计聚合。

统计口径（运营大屏）：
- 用户：sys_user（排除已软删）
- 测评：assessment_record（completed）
- 报告：assessment_report（按 level 分档）
- 智能体调用：chat_message（assistant 消息数）
- 知识库：knowledge_files
- 咨询：consultation
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from app.models.mysql.platform import (
    AssessmentRecord,
    AssessmentReport,
    ChatMessage,
    Consultation,
    DietitianProfile,
    OperationLog,
    SystemConfig,
)
from app.models.mysql.knowledge_file import KnowledgeFile
from app.models.mysql.user import SysUser
from app.utils.db import get_session

logger = logging.getLogger(__name__)


class AdminDAO:
    # ---------------- 用户 ---------------- #
    @staticmethod
    def list_users(role: Optional[str] = None, status: Optional[str] = None,
                   keyword: Optional[str] = None, limit: int = 50,
                   offset: int = 0, session=None) -> List[SysUser]:
        own = session is None
        s = session or get_session()
        try:
            q = s.query(SysUser).filter(SysUser.deleted_at.is_(None))
            if role:
                q = q.filter_by(role=role)
            if status:
                q = q.filter_by(status=status)
            if keyword:
                like = f"%{keyword}%"
                q = q.filter(SysUser.username.like(like) | SysUser.real_name.like(like))
            # 深分页靠数据库的 offset/limit 完成：page=5000 由 DB 快速判定越界，
            # 不把全表拉进内存再切片（IT-AD-07）
            return q.order_by(SysUser.id.desc()).offset(offset).limit(limit).all()
        finally:
            if own:
                s.close()

    @staticmethod
    def count_users(role: Optional[str] = None, status: Optional[str] = None,
                    keyword: Optional[str] = None, session=None) -> int:
        """筛选条件下的**真实总数**。

        原来把 total 设成"本页返回条数"，前端据此算页数会一直停在第 1 页，
        超过 limit 的用户永远翻不到。
        """
        from sqlalchemy import func
        own = session is None
        s = session or get_session()
        try:
            q = s.query(func.count(SysUser.id)).filter(SysUser.deleted_at.is_(None))
            if role:
                q = q.filter_by(role=role)
            if status:
                q = q.filter_by(status=status)
            if keyword:
                like = f"%{keyword}%"
                q = q.filter(SysUser.username.like(like) | SysUser.real_name.like(like))
            return int(q.scalar() or 0)
        finally:
            if own:
                s.close()

    @staticmethod
    def get_user(user_id: str, session=None) -> Optional[SysUser]:
        own = session is None
        s = session or get_session()
        try:
            return s.query(SysUser).filter_by(user_id=str(user_id)).first()
        finally:
            if own:
                s.close()

    @staticmethod
    def set_user_status(user_id: str, status: str, session=None) -> Optional[SysUser]:
        own = session is None
        s = session or get_session()
        try:
            u = s.query(SysUser).filter_by(user_id=str(user_id)).first()
            if not u:
                return None
            u.status = status
            if own:
                s.commit()
                s.refresh(u)
            return u
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    # ---------------- 配置 ---------------- #
    @staticmethod
    def list_configs(session=None) -> List[SystemConfig]:
        own = session is None
        s = session or get_session()
        try:
            return s.query(SystemConfig).order_by(SystemConfig.config_key.asc()).all()
        finally:
            if own:
                s.close()

    @staticmethod
    def get_config(key: str, session=None) -> Optional[SystemConfig]:
        own = session is None
        s = session or get_session()
        try:
            return s.query(SystemConfig).filter_by(config_key=key).first()
        finally:
            if own:
                s.close()

    @staticmethod
    def set_config(key: str, value: str, desc: Optional[str], operator: str,
                   session=None) -> SystemConfig:
        own = session is None
        s = session or get_session()
        try:
            row = s.query(SystemConfig).filter_by(config_key=key).first()
            if row:
                row.config_value = value
                if desc is not None:
                    row.description = desc
                row.updated_by = str(operator)
            else:
                row = SystemConfig(config_key=key, config_value=value,
                                   description=desc, updated_by=str(operator))
                s.add(row)
            if own:
                s.commit()
                s.refresh(row)
            else:
                s.flush()
            return row
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    # ---------------- 操作日志 ---------------- #
    @staticmethod
    def add_log(operator_id: str, operator_role: str, action: str,
                target_type: Optional[str] = None, target_id: Optional[str] = None,
                content: Optional[dict] = None, ip: Optional[str] = None,
                session=None) -> OperationLog:
        own = session is None
        s = session or get_session()
        try:
            log = OperationLog(operator_id=str(operator_id), operator_role=operator_role,
                               action=action, target_type=target_type,
                               target_id=str(target_id) if target_id is not None else None,
                               content=content, ip_address=ip)
            s.add(log)
            if own:
                s.commit()
                s.refresh(log)
            else:
                s.flush()
            return log
        except SQLAlchemyError:
            if own:
                s.rollback()
            raise
        finally:
            if own:
                s.close()

    @staticmethod
    def list_logs(action: Optional[str] = None, limit: int = 100,
                  session=None) -> List[OperationLog]:
        own = session is None
        s = session or get_session()
        try:
            q = s.query(OperationLog)
            if action:
                q = q.filter_by(action=action)
            return q.order_by(OperationLog.id.desc()).limit(limit).all()
        finally:
            if own:
                s.close()

    # ---------------- 统计聚合 ---------------- #
    @staticmethod
    def stats(session=None) -> Dict[str, Any]:
        own = session is None
        s = session or get_session()
        try:
            users = s.query(func.count(SysUser.id)).filter(SysUser.deleted_at.is_(None)).scalar() or 0
            dietitians = (s.query(func.count(DietitianProfile.id))
                          .filter_by(audit_status="approved").scalar() or 0)
            pending_dietitians = (s.query(func.count(DietitianProfile.id))
                                  .filter_by(audit_status="pending").scalar() or 0)
            records = s.query(func.count(AssessmentRecord.id)).scalar() or 0
            completed = (s.query(func.count(AssessmentRecord.id))
                         .filter_by(status="completed").scalar() or 0)
            reports = s.query(func.count(AssessmentReport.id)).scalar() or 0
            chats = s.query(func.count(ChatMessage.id)).scalar() or 0
            consults = s.query(func.count(Consultation.id)).scalar() or 0
            docs = s.query(func.count(KnowledgeFile.id)).scalar() or 0
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            new_users_today = (s.query(func.count(SysUser.id))
                               .filter(SysUser.created_at >= today).scalar() or 0)

            level_rows = (s.query(AssessmentReport.level, func.count(AssessmentReport.id))
                          .group_by(AssessmentReport.level).all())
            level_dist = {lv or "unknown": cnt for lv, cnt in level_rows}

            # 近 7 日测评趋势
            trend = []
            for i in range(6, -1, -1):
                day = today - timedelta(days=i)
                nxt = day + timedelta(days=1)
                n = (s.query(func.count(AssessmentRecord.id))
                     .filter(AssessmentRecord.submitted_at >= day,
                             AssessmentRecord.submitted_at < nxt).scalar() or 0)
                trend.append({"date": day.strftime("%Y-%m-%d"), "count": int(n)})

            avg_score = s.query(func.avg(AssessmentReport.score)).scalar()
            return {
                "users": int(users), "new_users_today": int(new_users_today),
                "dietitians": int(dietitians), "pending_dietitians": int(pending_dietitians),
                "assessments": int(records), "completed_assessments": int(completed),
                "reports": int(reports), "chat_messages": int(chats),
                "consultations": int(consults), "knowledge_docs": int(docs),
                "level_distribution": level_dist,
                "avg_score": round(float(avg_score), 2) if avg_score is not None else None,
                "assessment_trend_7d": trend,
            }
        except Exception as e:
            logger.warning(f"统计聚合失败：{e}")
            return {"error": str(e)}
        finally:
            if own:
                s.close()
