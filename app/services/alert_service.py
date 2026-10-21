# -*- coding: utf-8 -*-
"""预警求值与告警落地（FR-V-10）。

## 这个模块补的是哪一环

阈值管理（`GET/PUT /admin/alerts/thresholds`）早就有了：管理员能改、
越界会收敛、改动留审计。但**没有任何地方读它去算**——配了 `dau_drop_pct=10`，
系统并不会去算日环比，也不会告警。配置页看起来一切正常，实际上是空转的。

本模块做三件事：
  1. **求值**：按阈值口径算实际值（DAU 连续下滑、低分报告占比、咨询超时、
     注销清理超期）；
  2. **留痕**：命中就写 `ops_alert`，按「项 + 日期」去重，同一天不重复刷；
  3. **触达**：给管理员/运营推站内通知，并支持可选 webhook（企业微信/钉钉）。

## 为什么按天去重

定时作业每小时跑一次。如果不去重，一个持续超标 8 小时的指标会堆 8 条一样的预警，
运营很快就会把这个列表当噪音，然后真的出问题时也不看了。
按天去重之后，同一天的同一项只留一条，但**值更新为新算出的值**——
既能看到"今天报过"，也能看到"今天最后一次算出来是多少"。

## 为什么阈值全部从库里读，不写进代码

阈值是运营口径，会随业务阶段调整（冷启动期 DAU 跌 10% 很正常，
成熟期跌 10% 就是事故）。写进代码意味着每次调整都要发版，
而发版在健康类系统里是有停机成本的。所以代码里只有 `rules.ALERT_THRESHOLDS`
的**默认值与合法区间**，实际生效值永远以 `system_config.alert.thresholds` 为准。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func

from app.core import rules
from app.dao.admin_dao import AdminDAO
from app.dao.assessment_dao import session_scope
from app.models.mysql.platform import AssessmentReport, Consultation, DataDeletionTask, OpsAlert
from app.models.mysql.user import SysUser

logger = logging.getLogger("AlertService")

# webhook 请求超时（秒）：告警是旁路，不能因为 webhook 慢把作业拖住
WEBHOOK_TIMEOUT = 5


def _load_thresholds(session) -> Dict[str, float]:
    """读取生效阈值；缺项回退默认值，非法值按区间收敛。"""
    row = AdminDAO.get_config(rules.ALERT_CONFIG_KEY, session=session)
    saved: Dict[str, Any] = {}
    if row and row.config_value:
        try:
            saved = json.loads(row.config_value) or {}
        except (ValueError, TypeError):
            saved = {}
    return {key: rules.clamp_alert(key, saved.get(key, spec["default"]))
            for key, spec in rules.ALERT_THRESHOLDS.items()}


# --------------------------------------------------------------------------- #
# 四个求值口径
# --------------------------------------------------------------------------- #
def _check_dau_drop(session, threshold: float) -> Optional[Dict[str, Any]]:
    """DAU 连续 3 日环比下降超过阈值。

    口径必须写死成"连续 3 日"，否则单日波动（周末、节假日）会天天报。
    这里取最近 4 天的 DAU，算后 3 天各自的环比。
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    series: List[Dict[str, Any]] = []
    for i in range(3, -1, -1):
        day = today - timedelta(days=i)
        n = AdminDAO._active_users(session, day)
        series.append({"date": day.strftime("%Y-%m-%d"), "dau": int(n)})

    drops: List[float] = []
    for prev, cur in zip(series, series[1:]):
        if prev["dau"] <= 0:
            # 分母为 0 时环比无意义（新上线/停摆）。按 0 处理而不是算成 100%，
            # 否则上线第二天必定误报一条 critical。
            drops.append(0.0)
        else:
            drops.append(round((prev["dau"] - cur["dau"]) / prev["dau"] * 100, 2))

    breached = all(d > threshold for d in drops)
    if not breached:
        return None
    return {
        "alert_key": "dau_drop_pct", "level": "warn",
        "title": f"日活连续 3 日环比下降超过 {threshold}%",
        "value": f"{drops[-1]}%", "threshold": f"{threshold}%",
        "detail": {"series": series, "drops_pct": drops, "rule": "连续 3 日环比均超阈值"},
    }


def _check_score_low(session, threshold: float) -> Optional[Dict[str, Any]]:
    """「待改善」等级报告占比超过阈值。"""
    total = session.query(func.count(AssessmentReport.id)).scalar() or 0
    if not total:
        return None
    low = (session.query(func.count(AssessmentReport.id))
           .filter(AssessmentReport.level == "needs_improvement").scalar() or 0)
    ratio = round(low / total * 100, 2)
    if ratio <= threshold:
        return None
    return {
        "alert_key": "score_low_ratio", "level": "warn",
        "title": f"低分报告占比 {ratio}% 超过阈值 {threshold}%",
        "value": f"{ratio}%", "threshold": f"{threshold}%",
        "detail": {"low": int(low), "total": int(total),
                   "rule": "level=needs_improvement 的报告占全部报告比例"},
    }


def _check_consult_timeout(session, threshold: float) -> Optional[Dict[str, Any]]:
    """待接入咨询超过阈值分钟数仍无人接手。

    只统计 `pending`：`active` 是已接手在聊，`closed/timeout` 是已结束，
    把它们算进"超时未接手"会让这个指标永远红着。
    """
    cutoff = datetime.now() - timedelta(minutes=threshold)
    rows = (session.query(Consultation.id, Consultation.created_at)
            .filter(Consultation.status == "pending", Consultation.created_at < cutoff)
            .order_by(Consultation.created_at.asc()).all())
    if not rows:
        return None
    oldest = rows[0][1]
    wait_min = int((datetime.now() - oldest).total_seconds() // 60) if oldest else None
    return {
        "alert_key": "consult_timeout_min", "level": "critical" if len(rows) >= 5 else "warn",
        "title": f"{len(rows)} 条咨询待接入超过 {int(threshold)} 分钟",
        "value": f"{len(rows)} 条", "threshold": f"{int(threshold)} 分钟",
        "detail": {"pending_overdue": len(rows), "oldest_wait_minutes": wait_min,
                   "oldest_id": int(rows[0][0]), "rule": "status=pending 且创建时间超过阈值"},
    }


def _check_deletion_overdue(session) -> Optional[Dict[str, Any]]:
    """注销清理超期任务数（合规 D2：15 日内完成）。

    这一项没有对应阈值键（它不该允许运营调大），所以用固定口径：
    只要存在 `execute_at` 已过且未完成的任务就报 critical。
    它和大屏上的"超期"数字是同一个口径，不会出现"页面显示超期、预警说没有"。
    """
    now = datetime.now()
    rows = (session.query(DataDeletionTask.id, DataDeletionTask.user_id,
                          DataDeletionTask.execute_at)
            .filter(DataDeletionTask.execute_at < now,
                    DataDeletionTask.state.in_(("pending", "running", "failed")))
            .order_by(DataDeletionTask.execute_at.asc()).all())
    if not rows:
        return None
    oldest = rows[0][2]
    overdue_days = (now - oldest).days if oldest else None
    return {
        "alert_key": "deletion_overdue", "level": "critical" if (overdue_days or 0) >= 1 else "warn",
        "title": f"{len(rows)} 个注销清理任务已超期未完成",
        "value": f"{len(rows)} 个", "threshold": f"注销后 {rules.DELETION_ALERT_DAYS} 日内",
        "detail": {"overdue": len(rows), "oldest_execute_at": str(oldest),
                   "oldest_overdue_days": overdue_days,
                   "rule": "execute_at 已过且 state 未到 done"},
    }


# --------------------------------------------------------------------------- #
# 落库 + 触达
# --------------------------------------------------------------------------- #
def _upsert(session, item: Dict[str, Any], today: str) -> str:
    """写一条预警；同一天同一项已存在则更新值。

    返回本次变化类型：
      · `new`        今天第一次出现 -> 要推通知；
      · `escalated`  同一项等级升高（warn -> critical）-> 要再推一次；
      · `same`       只是数值刷新 -> **不推**。

    为什么要区分：作业每小时跑一次，命中项会一直命中。如果每次命中都推站内信，
    一个持续超标的指标一天能给管理员刷 24 条一样的通知，第二天他就会把这个入口
    当噪音，真的出问题时也不会看。去重表 + 只在"新出现/升级"时触达，才有信号价值。
    """
    existing = (session.query(OpsAlert)
                .filter(OpsAlert.alert_key == item["alert_key"],
                        OpsAlert.dedup_key == today).first())
    if existing is not None:
        change = "same"
        if _level_rank(item["level"]) > _level_rank(existing.level):
            change = "escalated"
        existing.value = item["value"]
        existing.threshold = item["threshold"]
        existing.detail = item["detail"]
        existing.level = item["level"]
        existing.title = item["title"]
        return change
    session.add(OpsAlert(
        alert_key=item["alert_key"], dedup_key=today, level=item["level"],
        title=item["title"], detail=item["detail"], value=item["value"],
        threshold=item["threshold"], state="open",
    ))
    return "new"


def _level_rank(level: str) -> int:
    return {"info": 0, "warn": 1, "critical": 2}.get(str(level), 0)


def _notify_admins(session, item: Dict[str, Any]) -> int:
    """给管理员/运营推站内通知。webhook 未配置时只落库 + 站内信。"""
    from app.services.notification_service import notification_service

    rows = (session.query(SysUser.user_id)
            .filter(SysUser.role.in_(("admin", "operator")),
                    SysUser.status == "active").all())
    sent = 0
    content = (f"{item['title']}（实际 {item['value']}，阈值 {item['threshold']}）。"
               "可在管理后台「预警」页查看明细并确认处理。")
    for (uid,) in rows:
        if notification_service.push(uid, f"【预警】{item['title']}", content, ntype="system"):
            sent += 1
    return sent


def _send_webhook(item: Dict[str, Any]) -> str:
    """可选 webhook（企业微信/钉钉机器人）。未配置返回 unconfigured，不视为失败。"""
    from app.config import settings

    url = (settings.ALERT_WEBHOOK_URL or "").strip()
    if not url:
        return "unconfigured"
    try:
        import httpx

        payload = {"msgtype": "text", "text": {"content": f"【预警】{item['title']}"}}
        r = httpx.post(url, json=payload, timeout=WEBHOOK_TIMEOUT)
        return "ok" if r.status_code < 300 else f"http {r.status_code}"
    except Exception as e:                             # noqa: BLE001
        # 告警通道失败不能中断求值：预警已经落库了，通道失败只是"没推出去"
        logger.warning(f"预警 webhook 发送失败（不影响落库）：{e}")
        return f"fail: {type(e).__name__}"


def evaluate_alerts() -> Dict[str, Any]:
    """定时任务入口：求值 → 落库 → 触达。返回执行摘要。"""
    today = datetime.now().strftime("%Y-%m-%d")
    hits: List[Dict[str, Any]] = []
    with session_scope() as s:
        th = _load_thresholds(s)
        checks = [
            _check_dau_drop(s, float(th["dau_drop_pct"])),
            _check_score_low(s, float(th["score_low_ratio"])),
            _check_consult_timeout(s, float(th["consult_timeout_min"])),
            _check_deletion_overdue(s),
        ]
        for item in checks:
            if item:
                item["change"] = _upsert(s, item, today)
                hits.append(item)

    # 只有新出现或等级升高的才触达；数值刷新不打扰
    pushable = [h for h in hits if h.get("change") in ("new", "escalated")]
    notified, webhooks = 0, []
    for item in pushable:
        with session_scope() as s:
            notified += _notify_admins(s, item)
        webhooks.append({"key": item["alert_key"], "webhook": _send_webhook(item)})

    return {
        "date": today,
        "thresholds": {k: v for k, v in th.items()},
        "hit_count": len(hits),
        "new_count": sum(1 for h in hits if h.get("change") == "new"),
        "escalated_count": sum(1 for h in hits if h.get("change") == "escalated"),
        "hits": [{"key": h["alert_key"], "level": h["level"], "change": h.get("change"),
                  "title": h["title"], "value": h["value"]} for h in hits],
        "notified": notified,
        "webhook": webhooks,
    }


def list_alerts(state: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    """预警列表（管理后台「预警」页签的数据源）。"""
    with session_scope() as s:
        q = s.query(OpsAlert)
        if state:
            q = q.filter(OpsAlert.state == state)
        rows = q.order_by(OpsAlert.created_at.desc()).limit(max(1, min(limit, 200))).all()
        return {
            "items": [{
                "id": r.id, "alert_key": r.alert_key, "level": r.level,
                "title": r.title, "value": r.value, "threshold": r.threshold,
                "detail": r.detail, "state": r.state,
                "created_at": r.created_at, "acked_by": r.acked_by,
                "acked_at": r.acked_at,
            } for r in rows],
            "total": len(rows),
            "open_count": s.query(func.count(OpsAlert.id))
                           .filter(OpsAlert.state == "open").scalar() or 0,
        }


def ack_alert(alert_id: int, operator: dict) -> Dict[str, Any]:
    """确认预警：只改状态，不删记录（留痕比干净重要）。"""
    with session_scope() as s:
        row = s.query(OpsAlert).filter(OpsAlert.id == alert_id).first()
        if row is None:
            return {"acked": False, "reason": "预警不存在"}
        row.state = "ack"
        row.acked_by = str(operator.get("sub"))
        row.acked_at = datetime.now()
        return {"acked": True, "id": row.id, "state": row.state}
