# -*- coding: utf-8 -*-
"""站内消息通知服务（原型：顶栏「🔔 消息中心」）。

其它模块（测评完成 / 咨询回复 / 打卡提醒）可调用 `push()` 生成通知；
push 为**最佳努力**：失败只记日志，绝不阻断主业务流程。
"""
import logging
from typing import Any, Dict, List, Optional

from app.core.errors import BizError, ErrCode
from app.dao.notification_dao import NotificationDAO

logger = logging.getLogger("NotificationService")

NOTIFICATION_TYPES = ("risk", "checkin", "consultation", "system")


def _brief(n) -> Dict[str, Any]:
    return {
        "id": n.id,
        "type": n.type,
        "title": n.title,
        "content": n.content,
        "link": n.link,
        "read": n.read_at is not None,
        "read_at": n.read_at,
        "created_at": n.created_at,
    }


class NotificationService:
    @staticmethod
    def list_mine(user: Optional[dict], unread_only: bool = False,
                  limit: int = 50) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        uid = str(user.get("sub"))
        if not isinstance(limit, int) or limit < 1 or limit > 200:
            limit = 50
        rows = NotificationDAO.list_mine(uid, unread_only=unread_only, limit=limit)
        return {
            "items": [_brief(n) for n in rows],
            "total": len(rows),
            "unread": NotificationDAO.unread_count(uid),
        }

    @staticmethod
    def mark_read(user: Optional[dict], notification_id: int) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        uid = str(user.get("sub"))
        row = NotificationDAO.mark_read(uid, notification_id)
        if not row:
            # 非本人或不存在，统一 404 防枚举（评审 M3）
            raise BizError(ErrCode.RESOURCE_NOT_FOUND, "消息不存在")
        data = _brief(row)
        data["unread"] = NotificationDAO.unread_count(uid)
        return data

    @staticmethod
    def mark_all_read(user: Optional[dict]) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        uid = str(user.get("sub"))
        changed = NotificationDAO.mark_all_read(uid)
        return {"marked": changed, "unread": NotificationDAO.unread_count(uid)}

    @staticmethod
    def push(user_id: Optional[str], title: str, content: str = "",
             ntype: str = "system", link: Optional[str] = None) -> bool:
        """内部推送（最佳努力）。返回是否成功，失败不抛异常。"""
        if not user_id:
            return False
        try:
            NotificationDAO.create(str(user_id), title, content,
                                   ntype if ntype in NOTIFICATION_TYPES else "system", link)
            return True
        except Exception as e:                       # noqa: BLE001
            logger.warning(f"通知推送失败（不影响主流程）：{e}")
            return False


notification_service = NotificationService()
