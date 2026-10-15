# -*- coding: utf-8 -*-
"""食养计划、智能荐菜、智能体目录和舌象文件业务服务。

设计原则：
- **食谱骨架由规则生成**（确定性、可复现），LLM 只写"方案总述"文字（技术红线：不让它算营养/配餐逻辑）。
- LLM 不可用或超时 → 计划降级 `state=basic`，食谱照常返回，用户不被阻塞。
- 舌象 AI 辨识 **待 S4 Spike 验证后才启用**；当前仅做存储与状态管理（status=uploaded）。
- 智能体目录与五大场景均为配置驱动：读 `system_config`，缺省用内置定义。
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from app.core.errors import BizError, ErrCode
from app.dao.assessment_dao import ReportDAO, session_scope
from app.dao.plan_dao import PlanDAO, TongueDAO

logger = logging.getLogger("PlanService")

DISCLAIMER = "本内容为食疗建议，不替代医生诊疗与用药方案"

# 食谱骨架库（规则生成；可后续迁移到 system_config 由营养师维护）
# 菜库已迁移至 app/services/recipe_library.py（2026-09-11 扩充：每场景每餐 6-7 道）
from app.services.recipe_library import RECIPE_LIBRARY

# ---- 智能体中心：1 个主智能体（调度中枢）+ 6 个子智能体 ----
# 主智能体不直接产出内容，只负责识别需求意图、规划链路、把任务分派给六个子智能体并汇总。

PRIMARY_AGENT_KEY = "orchestrator"

PRIMARY_AGENT: Dict[str, Any] = {
    "key": PRIMARY_AGENT_KEY, "name": "智参主智能体", "role": "orchestrator",
    "desc": "调度中枢：识别需求意图、规划执行链路、分派六个子智能体并汇总结果",
    "enabled": True,
    "children": ["assessment", "diet", "dish", "steward", "dietitian", "data"],
}

SUB_AGENTS: List[Dict[str, Any]] = [
    {"key": "assessment", "name": "健康测评智能体", "role": "sub", "parent": PRIMARY_AGENT_KEY,
     "desc": "舌象体质、营养问卷、慢病指标、OCR 体检报告解析与健康风险预警", "enabled": True},
    {"key": "diet", "name": "食养食疗智能体", "role": "sub", "parent": PRIMARY_AGENT_KEY,
     "desc": "周期食养计划、药食同源知识融合、每日菜谱与采购清单", "enabled": True},
    {"key": "dish", "name": "智能荐菜智能体", "role": "sub", "parent": PRIMARY_AGENT_KEY,
     "desc": "体质/慢病/过敏综合匹配，生成家庭、宴请、团队菜单", "enabled": True},
    {"key": "steward", "name": "健康管家智能体", "role": "sub", "parent": PRIMARY_AGENT_KEY,
     "desc": "健康风险提醒、用餐与打卡提醒、健康饮品推荐", "enabled": True},
    {"key": "dietitian", "name": "营养师智能体", "role": "sub", "parent": PRIMARY_AGENT_KEY,
     "desc": "用户营养问答、读取健康档案上下文、辅助人工营养师咨询", "enabled": True},
    {"key": "data", "name": "数据分析智能体", "role": "sub", "parent": PRIMARY_AGENT_KEY,
     "desc": "Text2SQL、角色级行权限过滤、图表/AI 解读/智能报告", "enabled": True},
]

DEFAULT_AGENTS: List[Dict[str, Any]] = [PRIMARY_AGENT] + SUB_AGENTS

# 五大健康业务场景（口径以原型 index_v2.html 为准）
DEFAULT_SCENES: List[Dict[str, Any]] = [
    {"key": "home_diet", "name": "健康饮食管家", "icon": "home",
     "desc": "家庭食养、团队食养、外卖智订、健康饮品，支持多成员统一规划"},
    {"key": "chronic", "name": "慢病管理专家", "icon": "heart",
     "desc": "慢病分类档案、风险阈值、禁忌提醒、周期食养方案与打卡跟踪"},
    {"key": "banquet", "name": "社交宴请助手", "icon": "dining",
     "desc": "录入宾客慢病、过敏与体质，智能荐菜并标识风险，支持协同编辑"},
    {"key": "maternal", "name": "妇幼健康守护", "icon": "baby",
     "desc": "孕期、产后、婴幼儿专项测评与分阶段饮食指导，纳入家庭计划"},
    {"key": "travel", "name": "旅行美食智荐", "icon": "luggage",
     "desc": "出行前健康评估、目的地健康餐厅与美食推荐、途中饮食提醒"},
]


# ---- 主智能体调度规则（规则引擎，不经过大模型：结果可复现、可解释）----
# 命中关键词即纳入本次调度；order 是链路执行顺序：先评估 → 再立方案 → 再落地菜单
# → 再跟踪提醒 → 再答疑 → 最后统计。
DISPATCH_RULES: List[Dict[str, Any]] = [
    {"key": "assessment", "order": 1,
     "keywords": ["测评", "体检", "舌象", "问卷", "指标", "报告", "风险", "评估",
                  "体质", "血糖", "血压", "血脂", "尿酸", "身体情况", "健康档案"],
     "reason": "先摸清身体状况与风险，给后续方案定基线"},
    {"key": "diet", "order": 2,
     "keywords": ["食谱", "食养", "食疗", "计划", "方案", "一周", "周期", "菜谱",
                  "采购", "搭配", "调理", "养生"],
     "reason": "由它生成周期食养计划与采购清单"},
    {"key": "dish", "order": 3,
     "keywords": ["菜单", "荐菜", "吃什么", "推荐菜", "宴请", "聚餐", "家庭", "团队",
                  "外卖", "菜品", "饮品", "预算", "人数"],
     "reason": "由它按体质/慢病/过敏匹配具体菜品与菜单"},
    {"key": "steward", "order": 4,
     "keywords": ["提醒", "打卡", "跟踪", "通知", "坚持", "监督"],
     "reason": "方案要落到位，由它做提醒与打卡跟踪"},
    {"key": "dietitian", "order": 5,
     "keywords": ["咨询", "问答", "能不能吃", "可以吃吗", "为什么", "营养", "怎么吃",
                  "药食同源", "功效", "禁忌"],
     "reason": "属开放式营养问答，由它检索知识库并带引用作答"},
    {"key": "data", "order": 6,
     "keywords": ["统计", "报表", "图表", "多少", "趋势", "数据", "分析", "对比", "占比"],
     "reason": "需要跨表统计与可视化，由它走受控 Text2SQL"},
]


def _read_config(key: str) -> Optional[str]:
    try:
        from app.utils.db import get_session
        from app.models.mysql.platform import SystemConfig
        s = get_session()
        try:
            row = s.query(SystemConfig).filter_by(config_key=key).first()
            return row.config_value if row else None
        finally:
            s.close()
    except Exception as e:
        logger.warning(f"读取配置 {key} 失败：{e}")
        return None


def _pick_scene(constitution: Optional[str], symptoms: Optional[List[str]]) -> str:
    text = f"{constitution or ''} {' '.join(symptoms or [])}"
    for key in ("控糖", "高血压", "孕期", "痰湿"):
        if key in text:
            return key
    if any(k in text for k in ("糖", "血糖", "糖尿病")):
        return "控糖"
    return "通用"


class PlanService:
    # ---------------- L. 食养计划 ---------------- #
    @staticmethod
    def build_recipes(scene: str, days: int = 3,
                      avoid: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """规则生成食谱骨架；忌口食材会被过滤掉。"""
        lib = RECIPE_LIBRARY.get(scene) or RECIPE_LIBRARY["通用"]
        out: List[Dict[str, Any]] = []
        avoid = avoid or []

        def _hit(item: Dict[str, Any]) -> bool:
            """该菜是否命中忌口/过敏关键词。"""
            return any(a and a in ing for ing in item.get("ingredients", []) for a in avoid)

        # 先剔除整道命中忌口的菜（而不是仅删食材行，避免"有菜名无食材"），
        # 再在过滤后的池上轮换；全被过滤时回退全池，保证不缺餐。
        pools: Dict[str, List[Dict[str, Any]]] = {}
        for meal in ("breakfast", "lunch", "dinner"):
            pool_all = lib.get(meal) or []
            pool = [it for it in pool_all if not _hit(it)] or pool_all
            pools[meal] = pool

        from math import gcd

        def _step(n: int) -> int:
            """轮换步长：与池长互质，保证遍历全池且相邻天不重样。"""
            for cand in (n // 2, n // 2 + 1, 2, 1):
                if 1 <= cand < n and gcd(cand, n) == 1:
                    return cand
            return 0 if n <= 1 else 1

        steps = {m: _step(len(pools[m])) for m in ("breakfast", "lunch", "dinner")}
        for day in range(1, days + 1):
            for mi, meal in enumerate(("breakfast", "lunch", "dinner")):
                pool = pools[meal]
                if not pool:
                    continue
                item = pool[(mi * 3 + (day - 1) * steps[meal]) % len(pool)]
                ingredients = [i for i in item.get("ingredients", [])
                               if not any(a and a in i for a in avoid)]
                out.append({
                    "meal_type": meal, "day_no": day, "dish_name": item["dish_name"],
                    "ingredients": ingredients, "efficacy": item.get("efficacy"),
                    "caution": item.get("caution"),
                })
        return out

    @staticmethod
    async def generate_summary(plan_ctx: Dict[str, Any]) -> tuple:
        """LLM 生成方案总述；失败返回 (兜底文本, False)。"""
        prompt = (
            "你是药食同源食疗方案助手。依据下面的体质/慢病与食谱骨架，"
            "用不超过 250 字写出方案总述（调理思路、饮食原则、注意事项）。<arg_key:6124c78e>\n"
            f"体质：{plan_ctx.get('constitution') or '未判定'}\n"
            f"针对问题：{', '.join(plan_ctx.get('target_symptoms') or []) or '无'}\n"
            f"忌口：{', '.join(plan_ctx.get('avoid_foods') or []) or '无'}\n"
            f"食谱要点：{'; '.join(plan_ctx.get('recipe_names', [])[:6])}\n"
        )
        try:
            from app.services.report_service import report_service
            llm = report_service._ensure_llm()
            if llm is None:
                return PlanService.fallback_summary(plan_ctx, "模型服务未配置"), False
            text = await asyncio.to_thread(llm.invoke, prompt)
            text = (text.content if hasattr(text, "content") else str(text)).strip()
            if not text:
                return PlanService.fallback_summary(plan_ctx, "模型返回空内容"), False
            return text, True
        except Exception as e:
            logger.warning(f"食养方案总述生成失败（降级）：{e}")
            return PlanService.fallback_summary(plan_ctx, str(e)), False

    @staticmethod
    def fallback_summary(ctx: Dict[str, Any], reason: str = "") -> str:
        scene = ctx.get("scene") or "通用"
        return (f"【{scene}食养方案·基础版】\n"
                f"已依据你的体质与忌口生成 {ctx.get('days', 3)} 天食谱（见下方明细）。"
                "建议规律三餐、少油少盐、足量饮水，并观察身体反馈调整。\n"
                f"（个性化总述暂未生成：{reason or '服务繁忙'}，可稍后点击重新生成）")

    @classmethod
    async def create_plan(cls, user: dict, body: Dict[str, Any]) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        user_id = str(user.get("sub"))
        report_id = body.get("report_id")
        constitution = body.get("constitution")
        symptoms = body.get("target_symptoms") or []
        avoid = list(body.get("avoid_foods") or [])
        # 服务端自动叠加健康档案中的过敏/忌口关键词（规则读取数据库，非 LLM），
        # 保证网页/接口任何入口生成计划都会避开用户的过敏与忌口食材。
        try:
            import json as _json

            from app.models.mysql.platform import UserProfile

            with session_scope() as s:
                prof = s.query(UserProfile).filter_by(user_id=user_id).first()
                # 注意：必须在 session 内读取属性（expire_on_commit=True 下，
                # 关闭后访问会抛 DetachedInstanceError——本项目踩过的坑）
                raws = [getattr(prof, col, None) for col in ("allergies_json", "taboo_json")] if prof else []
            for raw in raws:
                if not raw:
                    continue
                for kw in (_json.loads(raw) or {}).get("keywords", []):
                    if kw and kw not in avoid:
                        avoid.append(kw)
        except Exception as e:
            logger.warning(f"读取健康档案过敏/忌口失败（忽略）：{e}")
        period = str(body.get("period") or "week").lower()
        if period not in ("week", "month"):
            raise BizError(ErrCode.PARAM_INVALID, "周期需为 week（周计划）/ month（月计划）")
        # 周计划默认 7 天、月计划默认 30 天；显式传 days 时以入参为准（上限 31）
        days = int(body.get("days") or (7 if period == "week" else 30))
        days = max(1, min(days, 31))
        period_name = "周" if period == "week" else "月"

        # 2026-09-11：未显式指定报告/体质时，自动融合最近一次测评结果
        # （报告等级→体质；待改善维度→针对问题），让任何入口生成的计划都贴合测评。
        if not report_id and not constitution:
            try:
                from app.services.dashboard_service import DashboardService
                risks = DashboardService.risks(user)
                if risks.get("report_id"):
                    report_id = risks["report_id"]
                    symptoms = symptoms + [r["name"] for r in risks.get("items", [])]
            except Exception as e:
                logger.warning(f"融合最近测评结果失败（忽略）：{e}")
        question = str(body.get("question") or "").strip()
        if question:
            # 用户本次咨询的问题参与场景匹配（如“我有高血压”→高血压场景）与总述撰写
            symptoms = symptoms + [question[:100]]


        with session_scope() as s:
            if report_id:
                rep = ReportDAO.get(int(report_id), session=s)
                if not rep or (rep.user_id and str(rep.user_id) != user_id):
                    raise BizError(ErrCode.REPORT_NOT_FOUND, "报告不存在")
                constitution = constitution or {"excellent": "平和", "good": "基本平和",
                                                "needs_improvement": "偏虚"}.get(rep.level)

        scene = _pick_scene(constitution, symptoms)
        recipes = cls.build_recipes(scene, days=days, avoid=avoid)
        ctx = {"constitution": constitution, "target_symptoms": symptoms,
               "avoid_foods": avoid, "scene": scene, "days": days,
               "recipe_names": [r["dish_name"] for r in recipes]}

        with session_scope() as s:
            plan = PlanDAO.create({
                "user_id": user_id, "report_id": report_id, "template_type": scene,
                "period": period,
                "title": f"{scene}食养{period_name}方案（{days}天）", "constitution": constitution,
                "target_symptoms": symptoms, "avoid_foods": avoid,
                "state": "generating", "disclaimer": DISCLAIMER,
            }, session=s)
            PlanDAO.add_recipes(plan.id, recipes, session=s)
            plan_id = plan.id

        summary, ok = await cls.generate_summary(ctx)
        with session_scope() as s:
            PlanDAO.update(plan_id, {
                "summary_text": summary,
                "state": "complete" if ok else "basic",
            }, session=s)
        # 必须等事务提交后再读，否则新 session 读不到未提交的状态（会拿到 generating）
        return cls.detail(user, plan_id)

    @staticmethod
    def shopping_list(user: dict, plan_id: int) -> Dict[str, Any]:
        """采购清单：按食材聚合去重（原型 diet 页「采购清单 18 项」）。"""
        with session_scope() as s:
            plan = PlanDAO.get(plan_id, session=s)
            if not plan or (str(plan.user_id) != str(user.get("sub"))
                            and user.get("role") not in ("admin", "nutritionist", "dietitian")):
                raise BizError(ErrCode.RESOURCE_NOT_FOUND, "方案不存在")
            # 会话内一次性取成纯 Python 值：会话关闭/提交后再访问 ORM 属性会 DetachedInstanceError
            title = plan.title
            avoid = {str(x) for x in (plan.avoid_foods or [])}
            raw_ingredients: List[Any] = []
            for r in PlanDAO.list_recipes(plan_id, session=s):
                raw_ingredients.extend(list(r.ingredients or []))

        agg: Dict[str, int] = {}
        for ing in raw_ingredients:
            if isinstance(ing, dict):
                name = str(ing.get("name") or ing.get("ingredient") or "").strip()
            else:
                name = str(ing).strip()
            if not name or name in avoid:
                continue
            agg[name] = agg.get(name, 0) + 1
        items = [{"name": k, "used_times": v} for k, v in
                 sorted(agg.items(), key=lambda x: (-x[1], x[0]))]
        return {"plan_id": plan_id, "title": title, "items": items,
                "total": len(items), "avoid_foods": sorted(avoid)}

    @staticmethod
    def detail(user: dict, plan_id: int) -> Dict[str, Any]:
        with session_scope() as s:
            plan = PlanDAO.get(plan_id, session=s)
            if not plan or (str(plan.user_id) != str(user.get("sub"))
                            and user.get("role") not in ("admin", "nutritionist", "dietitian")):
                raise BizError(ErrCode.RESOURCE_NOT_FOUND, "方案不存在")
            return PlanService._brief(plan, PlanDAO.list_recipes(plan_id, session=s))

    @staticmethod
    def _brief(plan, recipes) -> Dict[str, Any]:
        grouped: Dict[int, Dict[str, Any]] = {}
        for r in recipes:
            grouped.setdefault(r.day_no, {"day_no": r.day_no, "meals": []})
            grouped[r.day_no]["meals"].append({
                "meal_type": r.meal_type, "dish_name": r.dish_name,
                "ingredients": r.ingredients, "efficacy": r.efficacy, "caution": r.caution,
            })
        return {
            "plan_id": plan.id,
            "title": plan.title,
            "template_type": plan.template_type,
            "period": getattr(plan, "period", "week"),
            "constitution": plan.constitution,
            "target_symptoms": plan.target_symptoms,
            "avoid_foods": plan.avoid_foods,
            "summary_text": plan.summary_text,
            "state": plan.state,
            "report_id": plan.report_id,
            "days": sorted(grouped.values(), key=lambda x: x["day_no"]),
            "disclaimer": plan.disclaimer or DISCLAIMER,
            "generated_at": plan.generated_at,
            "degraded": plan.state == "basic",
        }

    @staticmethod
    def list_mine(user: dict, limit: int = 20) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        with session_scope() as s:
            rows = PlanDAO.list_by_user(str(user.get("sub")), limit, session=s)
            return {"items": [{"plan_id": p.id, "title": p.title, "state": p.state,
                               "template_type": p.template_type,
                               "period": getattr(p, "period", "week"),
                               "generated_at": p.generated_at} for p in rows],
                    "total": len(rows)}

    @staticmethod
    async def update(user: dict, plan_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        with session_scope() as s:
            plan = PlanDAO.get(plan_id, session=s)
            if not plan or str(plan.user_id) != str(user.get("sub")):
                raise BizError(ErrCode.RESOURCE_NOT_FOUND, "方案不存在")
            PlanDAO.update(plan_id, {k: v for k, v in data.items()
                                     if k in ("title", "constitution", "target_symptoms",
                                              "avoid_foods", "summary_text")}, session=s)
        return PlanService.detail(user, plan_id)

    @classmethod
    async def regenerate(cls, user: dict, plan_id: int) -> Dict[str, Any]:
        with session_scope() as s:
            plan = PlanDAO.get(plan_id, session=s)
            if not plan or str(plan.user_id) != str(user.get("sub")):
                raise BizError(ErrCode.RESOURCE_NOT_FOUND, "方案不存在")
            ctx = {"constitution": plan.constitution,
                   "target_symptoms": plan.target_symptoms or [],
                   "avoid_foods": plan.avoid_foods or [],
                   "scene": plan.template_type or "通用", "days": 3,
                   "recipe_names": [r.dish_name for r in PlanDAO.list_recipes(plan_id, session=s)]}
        summary, ok = await cls.generate_summary(ctx)
        with session_scope() as s:
            PlanDAO.update(plan_id, {"summary_text": summary,
                                     "state": "complete" if ok else "basic"}, session=s)
        return cls.detail(user, plan_id)

    # ---------------- M. 智能荐菜 ---------------- #
    @staticmethod
    def recommend_dishes(query: str, avoid: Optional[List[str]] = None,
                         constitution: Optional[str] = None) -> Dict[str, Any]:
        """荐菜：RAG 检索食材/菜谱语料 + 规则过滤忌口（不调 LLM 自由发挥）。"""
        if not query and not constitution:
            raise BizError(ErrCode.PARAM_INVALID, "请提供想吃的食材/菜式或体质")
        refs: List[Dict] = []
        try:
            from app.services.chat_query_service import chat_query_service
            refs = chat_query_service.retrieve(query or f"{constitution}体质 食疗", top_k=4) or []
            for r in refs:
                r["fragment"] = (r.get("fragment") or "")[:160]
        except Exception as e:
            logger.warning(f"荐菜检索失败（忽略）：{e}")

        scene = _pick_scene(constitution, None)
        recipes = PlanService.build_recipes(scene, days=1, avoid=avoid)
        return {
            "query": query, "constitution": constitution, "avoid_foods": avoid or [],
            "dishes": recipes,
            "references": refs,
            "disclaimer": DISCLAIMER,
            "note": "推荐基于知识库检索与规则库，具体请结合个人情况或咨询营养师",
        }

    @staticmethod
    def search_ingredients(keyword: str, top_k: int = 8) -> Dict[str, Any]:
        """食材库检索（依赖 K 组已灌入的食材语料）。"""
        if not keyword:
            raise BizError(ErrCode.PARAM_INVALID, "缺少关键字")
        refs: List[Dict] = []
        try:
            from app.services.chat_query_service import chat_query_service
            refs = chat_query_service.retrieve(f"{keyword} 营养成分 功效", top_k=top_k) or []
            for r in refs:
                r["fragment"] = (r.get("fragment") or "")[:200]
        except Exception as e:
            logger.warning(f"食材检索失败：{e}")
        return {"keyword": keyword, "items": refs, "total": len(refs),
                "note": "知识库未灌入食材语料时返回空，需先由管理员上传食材库文档"}

    # ---------------- N. 智能体中心 ---------------- #
    @staticmethod
    def agents() -> Dict[str, Any]:
        raw = _read_config("agent.catalog")
        if raw:
            try:
                items = json.loads(raw)
                if isinstance(items, list) and items:
                    primary = next((a.get("key") for a in items
                                    if a.get("role") == "orchestrator"), None)
                    return {"items": items, "total": len(items),
                            "primary_key": primary, "source": "config"}
            except Exception as e:
                logger.warning(f"agent.catalog 解析失败，使用内置目录：{e}")
        return {"items": DEFAULT_AGENTS, "total": len(DEFAULT_AGENTS),
                "primary_key": PRIMARY_AGENT_KEY, "source": "default"}

    @staticmethod
    def agent_detail(key: str) -> Dict[str, Any]:
        for a in PlanService.agents()["items"]:
            if a.get("key") == key:
                return a
        raise BizError(ErrCode.AGENT_UNKNOWN, "智能体不存在")

    # ---------------- 主智能体调度 ---------------- #
    @staticmethod
    def dispatch(text: str) -> Dict[str, Any]:
        """主智能体按规则生成调度方案：命中哪些子智能体、按什么顺序、为什么。

        纯规则匹配（关键词命中 + 链路顺序），不调用大模型，结果可复现、可解释；
        这是「调度决策」，按项目红线不交给 LLM 算。
        """
        raw = (text or "").strip()
        hit: Dict[str, List[str]] = {}
        for rule in DISPATCH_RULES:
            terms = [kw for kw in rule["keywords"] if kw in raw]
            if terms:
                hit[rule["key"]] = terms

        fallback = not hit
        if fallback:
            # 意图不明时不硬猜，交给通用营养问答兜底，避免空转调度
            hit = {"dietitian": []}

        by_key = {a["key"]: a for a in SUB_AGENTS}
        plan: List[Dict[str, Any]] = []
        for rule in sorted(DISPATCH_RULES, key=lambda r: r["order"]):
            k = rule["key"]
            if k not in hit:
                continue
            a = by_key[k]
            plan.append({
                "order": rule["order"], "key": k, "name": a["name"],
                "desc": a["desc"],
                "reason": ("未命中具体意图关键词，按通用营养问答兜底" if fallback
                           else rule["reason"]),
                "matched_terms": hit[k],
            })

        return {
            "request": raw,
            "primary": {"key": PRIMARY_AGENT_KEY, "name": PRIMARY_AGENT["name"],
                        "children": PRIMARY_AGENT["children"]},
            "plan": plan,
            "matched": len(plan),
            "fallback": fallback,
            "strategy": "先评估身体状况 → 再生成食养方案 → 再落地菜品菜单 → 再提醒跟踪 → 再答疑 → 最后统计",
            "note": "调度由规则引擎完成（关键词命中 + 链路顺序），不经过大模型，结果可复现",
        }

    # ---------------- 五大健康场景 ---------------- #
    @staticmethod
    def scenes() -> Dict[str, Any]:
        raw = _read_config("scene.config")
        if raw:
            try:
                items = json.loads(raw)
                if isinstance(items, list) and items:
                    return {"items": items, "total": len(items), "source": "config"}
            except Exception as e:
                logger.warning(f"scene.config 解析失败，使用内置场景：{e}")
        return {"items": DEFAULT_SCENES, "total": len(DEFAULT_SCENES), "source": "default"}

    # ---------------- O. 舌象图片 ---------------- #
    @staticmethod
    def upload_tongue(user: dict, file_obj, filename: str,
                      record_id: Optional[int] = None) -> Dict[str, Any]:
        """上传舌图到对象存储；AI 辨识待 S4 验证后启用（当前 status=uploaded）。"""
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        url = None
        note = None
        try:
            from app.utils.minio_client import get_minio
            url = get_minio().upload_file(file_obj, filename)
        except Exception as e:
            # 存储不可用不阻断：仍建记录，标记 failed，便于稍后重试
            logger.warning(f"舌图上传对象存储失败：{e}")
            note = f"对象存储不可用：{e}"
        if not url and not note:
            note = "对象存储未返回地址"
        with session_scope() as s:
            img = TongueDAO.create({
                "user_id": str(user.get("sub")),
                "record_id": record_id,
                "image_url": url or "",
                "status": "failed" if not url else "uploaded",
            }, session=s)
            return {"image_id": img.id, "image_url": img.image_url, "status": img.status,
                    "recognizable": False,
                    "note": note or "AI 辨识待 S4 验证后启用"}

    @staticmethod
    def get_tongue(user: dict, image_id: int) -> Dict[str, Any]:
        with session_scope() as s:
            img = TongueDAO.get(image_id, session=s)
            if not img or (img.user_id and str(img.user_id) != str(user.get("sub"))
                           and user.get("role") not in ("admin", "nutritionist", "dietitian")):
                raise BizError(ErrCode.RESOURCE_NOT_FOUND, "舌象记录不存在")
            return {"image_id": img.id, "image_url": img.image_url, "status": img.status,
                    "recognized_result": img.recognized_result, "model_name": img.model_name,
                    "record_id": img.record_id, "created_at": img.created_at,
                    "note": "AI 辨识待 S4 验证后启用"}


plan_service = PlanService()
