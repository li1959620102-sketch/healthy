# -*- coding: utf-8 -*-
"""Agent 人工审批状态机。"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, Optional

from app.agent_runtime.contract import AgentStatus
from app.agent_runtime.task_manager import db_persistence_allowed, task_manager


@dataclass
class ApprovalRequest:
    approval_id: str
    task_id: str
    step_id: str
    approval_type: str
    risk_level: str
    status: str = "pending"
    requested_by: Optional[str] = None
    approved_by: Optional[str] = None
    comment: Optional[str] = None
    expires_at: Optional[str] = None


class ApprovalManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: Dict[str, ApprovalRequest] = {}

    def request(self, task_id: str, step_id: str, approval_type: str,
                risk_level: str = "medium", requested_by: Optional[str] = None) -> ApprovalRequest:
        approval = ApprovalRequest(
            approval_id="approval_" + uuid.uuid4().hex,
            task_id=task_id, step_id=step_id, approval_type=approval_type,
            risk_level=risk_level, requested_by=requested_by,
            expires_at=(datetime.now() + timedelta(hours=24)).isoformat(timespec="seconds"),
        )
        with self._lock:
            self._items[approval.approval_id] = approval
        task_manager.set_status(task_id, AgentStatus.WAITING_APPROVAL,
                                current_step=step_id)
        self._persist(approval)
        return approval

    def get(self, approval_id: str) -> Optional[ApprovalRequest]:
        with self._lock:
            item = self._items.get(approval_id)
            if item is not None:
                return item
        if not db_persistence_allowed():
            return None
        try:
            from app.models.mysql.platform import AgentApproval
            from app.utils.db import get_session
            session = get_session()
            row = session.query(AgentApproval).filter_by(approval_id=approval_id).first()
            session.close()
            if row is None:
                return None
            item = ApprovalRequest(
                approval_id=row.approval_id, task_id=row.task_id, step_id=row.step_id,
                approval_type=row.approval_type, risk_level=row.risk_level or "medium",
                status=row.approval_status or "pending", requested_by=row.requested_by,
                approved_by=row.approved_by, comment=row.comment,
                expires_at=row.expires_at.isoformat(timespec="seconds") if row.expires_at else None,
            )
            with self._lock:
                self._items[approval_id] = item
            return item
        except Exception:
            return None

    def for_task(self, task_id: str):
        with self._lock:
            items = [item for item in self._items.values() if item.task_id == task_id]
        if items or not db_persistence_allowed():
            return items
        try:
            from app.models.mysql.platform import AgentApproval
            from app.utils.db import get_session
            session = get_session()
            rows = session.query(AgentApproval).filter_by(task_id=task_id).order_by(AgentApproval.id.asc()).all()
            session.close()
            items = [self.get(row.approval_id) for row in rows]
            return [item for item in items if item is not None]
        except Exception:
            return []

    def is_approved(self, task_id: str, step_id: str, approval_type: str) -> bool:
        with self._lock:
            return any(
                item.task_id == task_id and item.step_id == step_id
                and item.approval_type == approval_type and item.status == "approved"
                for item in self._items.values()
            )

    def decide(self, approval_id: str, approved: bool, operator_id: str,
               comment: str = "") -> ApprovalRequest:
        with self._lock:
            item = self._items.get(approval_id)
            if item is None:
                raise KeyError(f"审批不存在：{approval_id}")
            if item.status != "pending":
                raise ValueError(f"审批当前状态不可处理：{item.status}")
            item.status = "approved" if approved else "rejected"
            item.approved_by = operator_id
            item.comment = comment
        if approved:
            task_manager.set_status(item.task_id, AgentStatus.PAUSED,
                                    current_step=item.step_id)
        else:
            task_manager.set_status(item.task_id, AgentStatus.FAILED,
                                    current_step=item.step_id,
                                    error_code="AGENT_APPROVAL_REJECTED",
                                    error_message=comment or "人工审批拒绝")
        self._persist(item)
        return item

    def resume(self, approval_id: str) -> ApprovalRequest:
        item = self.get(approval_id)
        if item is None:
            raise KeyError(f"审批不存在：{approval_id}")
        if item.status != "approved":
            raise ValueError("只有已批准的审批才能恢复任务")
        task_manager.set_status(item.task_id, AgentStatus.RUNNING,
                                current_step=item.step_id)
        return item

    @staticmethod
    def _persist(item: ApprovalRequest) -> None:
        if not db_persistence_allowed():
            return
        try:
            from app.models.mysql.platform import AgentApproval
            from app.utils.db import get_session
            session = get_session()
            row = session.query(AgentApproval).filter_by(approval_id=item.approval_id).first()
            if row is None:
                row = AgentApproval(approval_id=item.approval_id)
                session.add(row)
            row.task_id = item.task_id
            row.step_id = item.step_id
            row.approval_type = item.approval_type
            row.risk_level = item.risk_level
            row.approval_status = item.status
            row.requested_by = item.requested_by
            row.approved_by = item.approved_by
            row.comment = item.comment
            row.expires_at = datetime.fromisoformat(item.expires_at) if item.expires_at else None
            row.approved_at = datetime.now() if item.status in {"approved", "rejected"} else None
            session.commit()
            session.close()
        except Exception:
            return


approval_manager = ApprovalManager()
