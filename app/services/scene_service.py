# -*- coding: utf-8 -*-
"""场景扩展服务：社交宴请助手 / 旅行美食智荐 / OCR 体检报告 / 大屏方案。

一期策略（用户确认）：**留桩 + 交互** ——
接口、状态、数据结构全部做齐，前端可完整走通流程；
依赖第三方或未达标模型的能力（OCR 引擎、目的地 POI）返回
`enabled=false` 或明确的 `note`，**绝不伪造结果**，二期只换实现不改契约。
"""
import logging
import time
from typing import Any, Dict, List, Optional

from app.core.errors import BizError, ErrCode
from app.dao.scene_dao import (
    BanquetGuestDAO, BanquetMenuDAO, DashboardLayoutDAO, OcrIndicatorDAO,
    OcrReportDAO, TravelPlanDAO,
)

logger = logging.getLogger("SceneService")

OCR_PENDING_NOTE = "OCR 引擎待接入：已保存文件与解析任务，识别结果暂不返回"
TRAVEL_PENDING_NOTE = "目的地餐厅/美食推荐待接入 POI 服务：当前返回基于健康档案的通用出行建议"
SNAPSHOT_PENDING_NOTE = "大屏截图需服务端无头浏览器渲染，待接入；当前返回布局与数据快照"


def _menu_brief(m, guests: List[Any]) -> Dict[str, Any]:
    return {
        "menu_id": m.id,
        "title": m.title,
        "party_date": m.party_date,
        "people_count": m.people_count,
        "budget": m.budget,
        "status": m.status,
        "dishes": m.dishes or [],
        "guests": [{"name": g.name, "chronic_tags": g.chronic_tags or [],
                    "allergies": g.allergies or [], "constitution": g.constitution}
                   for g in guests],
        "created_at": m.created_at,
    }


class SceneService:
    # ------------------------------------------------------------------ #
    # 社交宴请助手
    # ------------------------------------------------------------------ #
    @staticmethod
    def create_banquet(user: dict, body: Dict[str, Any]) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        title = (body.get("title") or "").strip()
        if not title:
            raise BizError(ErrCode.PARAM_INVALID, "宴请主题不能为空")
        guests: List[Dict[str, Any]] = body.get("guests") or []
        if len(guests) > 20:
            raise BizError(ErrCode.PARAM_INVALID, "宾客最多 20 位")

        # 汇总全体宾客的忌口，交给规则版荐菜过滤（确定性，不调 LLM 自由发挥）
        avoid: List[str] = []
        for g in guests:
            avoid.extend([str(x) for x in (g.get("allergies") or [])])
        avoid = sorted(set(avoid))

        from app.services.plan_service import PlanService
        try:
            recipes = PlanService.build_recipes("通用", days=1, avoid=avoid) or []
        except Exception as e:                       # noqa: BLE001
            logger.warning(f"宴请荐菜失败（返回空菜单，不阻断创建）：{e}")
            recipes = []

        # 风险标注：某道菜含某位宾客的过敏原 → 标记
        dishes = []
        for r in recipes:
            ings = [str(x.get("name") if isinstance(x, dict) else x)
                    for x in (r.get("ingredients") or [])]
            risks = []
            for g in guests:
                hit = set(ing.lower() for ing in ings) & \
                      set(str(a).lower() for a in (g.get("allergies") or []))
                if hit:
                    risks.append({"guest": g.get("name"), "allergens": sorted(hit)})
            dishes.append({"dish_name": r.get("dish_name"), "ingredients": ings,
                           "efficacy": r.get("efficacy"), "risks": risks})

        menu = BanquetMenuDAO.create({
            "user_id": str(user.get("sub")), "title": title,
            "party_date": body.get("party_date"), "people_count": body.get("people_count"),
            "budget": body.get("budget"), "constraints": body.get("constraints") or {},
            "dishes": dishes, "status": "draft",
        })
        BanquetGuestDAO.replace(menu.id, guests)
        return SceneService.get_banquet(user, menu.id)

    @staticmethod
    def _assert_menu_owner(m, user) -> None:
        if not m or (str(m.user_id) != str(user.get("sub"))
                     and user.get("role") not in ("admin", "nutritionist", "dietitian")):
            raise BizError(ErrCode.RESOURCE_NOT_FOUND, "宴请不存在")

    @staticmethod
    def get_banquet(user: dict, menu_id: int) -> Dict[str, Any]:
        m = BanquetMenuDAO.get(menu_id)
        SceneService._assert_menu_owner(m, user)
        return _menu_brief(m, BanquetGuestDAO.list_by_menu(menu_id))

    @staticmethod
    def list_banquets(user: dict, limit: int = 20) -> Dict[str, Any]:
        rows = BanquetMenuDAO.list_by_owner(str(user.get("sub")), limit=limit)
        return {"items": [_menu_brief(m, BanquetGuestDAO.list_by_menu(m.id)) for m in rows],
                "total": len(rows)}

    @staticmethod
    def update_banquet(user: dict, menu_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
        m = BanquetMenuDAO.get(menu_id)
        SceneService._assert_menu_owner(m, user)
        data = {k: v for k, v in body.items()
                if k in ("title", "party_date", "people_count", "budget", "status")}
        if data:
            BanquetMenuDAO.update(menu_id, data)
        if body.get("guests") is not None:
            BanquetGuestDAO.replace(menu_id, body["guests"])
        return SceneService.get_banquet(user, menu_id)

    # ------------------------------------------------------------------ #
    # 旅行美食智荐
    # ------------------------------------------------------------------ #
    @staticmethod
    def create_travel(user: dict, body: Dict[str, Any]) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        dest = (body.get("destination") or "").strip()
        if not dest:
            raise BizError(ErrCode.PARAM_INVALID, "目的地不能为空")
        notes = body.get("health_notes") or {}
        advice = {
            "enabled": False,
            "reason": TRAVEL_PENDING_NOTE,
            "general_tips": [
                "出行前确认慢病用药充足，随身携带病历或用药清单",
                "优先选择可备注少油少盐的餐厅，自带小包装坚果/全谷物应急",
                f"目的地：{dest}，注意当地饮食口味差异，避免一次性大量高糖高钠摄入",
            ],
            "avoid_foods": notes.get("avoid_foods") or [],
        }
        plan = TravelPlanDAO.create({
            "user_id": str(user.get("sub")), "destination": dest,
            "start_date": body.get("start_date"), "end_date": body.get("end_date"),
            "health_notes": notes, "advice": advice, "status": "planned",
        })
        return SceneService.get_travel(user, plan.id)

    @staticmethod
    def get_travel(user: dict, plan_id: int) -> Dict[str, Any]:
        p = TravelPlanDAO.get(plan_id)
        if not p or (str(p.user_id) != str(user.get("sub"))
                     and user.get("role") not in ("admin", "nutritionist", "dietitian")):
            raise BizError(ErrCode.RESOURCE_NOT_FOUND, "旅行计划不存在")
        return {"plan_id": p.id, "destination": p.destination,
                "start_date": p.start_date, "end_date": p.end_date,
                "health_notes": p.health_notes or {}, "advice": p.advice or {},
                "status": p.status, "created_at": p.created_at}

    @staticmethod
    def list_travel(user: dict, limit: int = 20) -> Dict[str, Any]:
        rows = TravelPlanDAO.list_by_owner(str(user.get("sub")), limit=limit)
        return {"items": [SceneService.get_travel(user, p.id) for p in rows],
                "total": len(rows)}

    # ------------------------------------------------------------------ #
    # OCR 体检报告解析（留桩 + 交互）
    # ------------------------------------------------------------------ #
    @staticmethod
    def upload_ocr(user: dict, file_obj, filename: str = "report.jpg") -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        url = None
        try:
            from app.utils.minio_client import get_minio
            url = get_minio().upload_file(file_obj, f"ocr/{int(time.time())}_{filename}")
        except Exception as e:                       # noqa: BLE001
            logger.warning(f"OCR 报告存储失败：{e}")
        rep = OcrReportDAO.create({
            "user_id": str(user.get("sub")), "file_name": filename, "file_url": url,
            "status": "uploaded", "note": OCR_PENDING_NOTE,
        })
        return {"report_id": rep.id, "file_url": url, "status": rep.status,
                "indicators": [], "recognizable": False, "note": OCR_PENDING_NOTE}

    @staticmethod
    def get_ocr(user: dict, report_id: int) -> Dict[str, Any]:
        r = OcrReportDAO.get(report_id)
        if not r or (str(r.user_id) != str(user.get("sub"))
                     and user.get("role") not in ("admin", "nutritionist", "dietitian")):
            raise BizError(ErrCode.RESOURCE_NOT_FOUND, "报告不存在")
        return {"report_id": r.id, "file_name": r.file_name, "file_url": r.file_url,
                "status": r.status, "recognizable": r.status == "done",
                "indicators": [{"name": i.name, "value": i.value, "unit": i.unit,
                                "ref_range": i.ref_range, "abnormal": bool(i.abnormal)}
                               for i in OcrIndicatorDAO.list_by_report(r.id)],
                "note": r.note or OCR_PENDING_NOTE, "created_at": r.created_at}

    @staticmethod
    def list_ocr(user: dict, limit: int = 20) -> Dict[str, Any]:
        rows = OcrReportDAO.list_by_owner(str(user.get("sub")), limit=limit)
        return {"items": [{"report_id": r.id, "file_name": r.file_name,
                           "status": r.status, "created_at": r.created_at} for r in rows],
                "total": len(rows), "note": OCR_PENDING_NOTE}

    # ------------------------------------------------------------------ #
    # 运营大屏方案
    # ------------------------------------------------------------------ #
    DEFAULT_LAYOUT = [
        {"widget": "kpi", "key": "dau", "title": "DAU", "span": 3},
        {"widget": "kpi", "key": "mau", "title": "MAU", "span": 3},
        {"widget": "kpi", "key": "month_assessment", "title": "本月测评", "span": 3},
        {"widget": "kpi", "key": "ai_ratio", "title": "智能咨询占比", "span": 3},
        {"widget": "line", "key": "scene_trend", "title": "各业务场景使用趋势",
         "span": 8, "row_span": 2},
        {"widget": "text", "key": "ai_insight", "title": "AI 运营洞察",
         "span": 4, "row_span": 2},
    ]

    @staticmethod
    def get_layout(user: dict, name: Optional[str] = None) -> Dict[str, Any]:
        uid = str(user.get("sub"))
        row = DashboardLayoutDAO.get_default(uid)
        if not row:
            return {"layout_id": None, "name": name or "平台日常运营大屏",
                    "is_default": True, "refresh_seconds": 300, "theme": "dark",
                    "highlight_abnormal": True, "layout": SceneService.DEFAULT_LAYOUT,
                    "source": "default"}
        return {"layout_id": row.id, "name": row.name, "is_default": bool(row.is_default),
                "refresh_seconds": row.refresh_seconds, "theme": row.theme,
                "highlight_abnormal": bool(row.highlight_abnormal),
                "layout": row.layout or SceneService.DEFAULT_LAYOUT, "source": "config"}

    @staticmethod
    def save_layout(user: dict, body: Dict[str, Any]) -> Dict[str, Any]:
        uid = str(user.get("sub"))
        name = (body.get("name") or "平台日常运营大屏").strip()
        payload = {
            "name": name,
            "refresh_seconds": int(body.get("refresh_seconds") or 300),
            "theme": body.get("theme") or "dark",
            "highlight_abnormal": 1 if body.get("highlight_abnormal", True) else 0,
            "layout": body.get("layout") or SceneService.DEFAULT_LAYOUT,
            "is_default": 1,
        }
        existing = DashboardLayoutDAO.get_default(uid)
        if existing:
            DashboardLayoutDAO.update(existing.id, payload)
            layout_id = existing.id
        else:
            row = DashboardLayoutDAO.create({**payload, "owner_id": uid})
            layout_id = row.id
        data = SceneService.get_layout(user)
        data["layout_id"] = layout_id
        return data

    @staticmethod
    def export_snapshot(user: dict) -> Dict[str, Any]:
        """导出大屏截图（留桩）：返回布局与数据快照，明确说明截图渲染待接入。"""
        return {"enabled": False, "note": SNAPSHOT_PENDING_NOTE,
                "snapshot": SceneService.get_layout(user)}


scene_service = SceneService()
