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

from sqlalchemy import func, select, union
from sqlalchemy.exc import SQLAlchemyError

from app.core import rules
from app.models.mysql.platform import (
    AssessmentRecord,
    AssessmentReport,
    AssessmentTemplate,
    ChatMessage,
    ChatSession,
    Consultation,
    ConsultationMessage,
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
    def _active_users(s, start: datetime) -> int:
        """窗口起点之后的**去重活跃用户数**（DAU/MAU 共用）。

        活跃 = 有过登录 ∪ 提交过测评 ∪ 发起过咨询 ∪ 发起过对话。
        为什么不只用 `last_login_at`：它记录的是"最近一次登录"，用户登录后
        保持会话继续使用（本项目 access token 2 小时、前端自动续期）期间
        不会再写登录记录，只用登录口径会把这些人算成"没来过"。

        UNION（而非 UNION ALL）本身就去重，故外层直接 COUNT(*)。
        """
        subs = [
            select(SysUser.user_id).where(SysUser.deleted_at.is_(None),
                                          SysUser.last_login_at.isnot(None),
                                          SysUser.last_login_at >= start),
            select(AssessmentRecord.user_id).where(AssessmentRecord.user_id.isnot(None),
                                                   AssessmentRecord.submitted_at >= start),
            select(Consultation.user_id).where(Consultation.user_id.isnot(None),
                                               Consultation.created_at >= start),
            select(ChatSession.user_id).where(ChatSession.user_id.isnot(None),
                                              ChatSession.created_at >= start),
        ]
        return int(s.query(func.count()).select_from(union(*subs).subquery()).scalar() or 0)

    @staticmethod
    def _template_usage(s) -> Dict[str, Any]:
        """测评模板使用率：每个模板被发起测评的次数与占比（故事 4）。

        「使用率」的两种读法里，本实现取原型文案支持的这一种：
        原型 AI 洞察写的是"某测评模板使用率连续下降"，主体是**单个模板**，
        所以给的是逐模板使用量与占比；同时给出汇总口径
        （多少模板被用过 / 已发布多少），便于一把看整体。

        分母只统计**归属到某个模板**的测评记录（template_id 非空）：
        把 NULL 混进分母会让所有模板占比加不到 1，对不上账。
        """
        rows = (s.query(AssessmentTemplate.id, AssessmentTemplate.code, AssessmentTemplate.name,
                        AssessmentTemplate.status, func.count(AssessmentRecord.id))
                .outerjoin(AssessmentRecord, AssessmentRecord.template_id == AssessmentTemplate.id)
                .group_by(AssessmentTemplate.id, AssessmentTemplate.code,
                          AssessmentTemplate.name, AssessmentTemplate.status)
                .order_by(func.count(AssessmentRecord.id).desc(), AssessmentTemplate.id.asc())
                .all())
        items = []
        total = 0
        for tid, code, name, status, cnt in rows:
            cnt = int(cnt or 0)
            total += cnt
            items.append({"template_id": int(tid), "code": code, "name": name,
                          "status": status, "records": cnt})
        for it in items:
            it["share"] = round(it["records"] / total, 4) if total else 0.0
        return {
            "total_records": total,
            "template_count": len(items),
            "used_template_count": sum(1 for i in items if i["records"] > 0),
            "published_template_count": sum(1 for i in items if i["status"] == "published"),
            "items": items,
        }

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

            # 运营活跃（故事 4）：DAU 当日窗口、MAU 近 30 日窗口
            dau = AdminDAO._active_users(s, today)
            mau = AdminDAO._active_users(s, today - timedelta(days=rules.ACTIVE_MAU_DAYS - 1))

            # 本月测评（自然月，与"近 7 日趋势"的滑动窗口口径区分开）
            month_start = today.replace(day=1)
            month_assessment = (s.query(func.count(AssessmentRecord.id))
                                .filter(AssessmentRecord.submitted_at >= month_start).scalar() or 0)

            # 智能咨询占比：营养师发出的咨询消息里由 AI 辅助起草的比例。
            # 分母只取 sender_type='dietitian'，避免把用户消息算进来把比例压扁。
            dt_msgs = (s.query(func.count(ConsultationMessage.id))
                       .filter(ConsultationMessage.sender_type == "dietitian").scalar() or 0)
            ai_msgs = (s.query(func.count(ConsultationMessage.id))
                       .filter(ConsultationMessage.sender_type == "dietitian",
                               ConsultationMessage.ai_assisted == 1).scalar() or 0)

            return {
                "users": int(users), "new_users_today": int(new_users_today),
                "dau": int(dau), "mau": int(mau),
                "month_assessment": int(month_assessment),
                "ai_ratio": round(ai_msgs / dt_msgs, 4) if dt_msgs else 0.0,
                "ai_ratio_note": f"智能咨询占比 = 营养师消息中 AI 辅助起草的比例（{ai_msgs}/{dt_msgs}）",
                "active_note": f"活跃口径：{rules.ACTIVE_MAU_DAYS} 日窗口内出现过登录/测评/咨询/对话的去重用户；"
                               "DAU 为当日窗口",
                "dietitians": int(dietitians), "pending_dietitians": int(pending_dietitians),
                "assessments": int(records), "completed_assessments": int(completed),
                "reports": int(reports), "chat_messages": int(chats),
                "consultations": int(consults), "knowledge_docs": int(docs),
                "level_distribution": level_dist,
                "avg_score": round(float(avg_score), 2) if avg_score is not None else None,
                "assessment_trend_7d": trend,
                "template_usage": AdminDAO._template_usage(s),
            }
        except Exception as e:
            logger.warning(f"统计聚合失败：{e}")
            return {"error": str(e)}
        finally:
            if own:
                s.close()
