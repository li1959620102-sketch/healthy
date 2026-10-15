# -*- coding: utf-8 -*-
"""评分引擎（第二批 · 规则算分）。

技术红线：**绝不让 LLM 算分**。所有分数由本模块确定性计算，LLM 只负责写分析文本。

C1 解耦策略（关键）：
    权重、等级阈值、评分版本全部从 `system_config` 读取，营养师顾问给出具体数值后
    **只改配置、不改代码**。配置项：
      - `scoring.weights`    JSON，形如 {"default": {"basic":0.4,"diet":0.35}, "TPL001": {...}}
      - `scoring.thresholds` JSON，形如 [{"min":80,"level":"excellent","name":"优秀"}, ...]
      - `scoring.version`    字符串，如 "v1.0"（入库到 assessment_report 便于追溯）
    读取失败（未配置 / 数据库不可用）时自动回退内置默认（等权 + 百分制三档），
    并在返回值里用 `config_source` 标明来源，便于 C1 闭环后核对。

百分制约定：
    单题得分 → 维度得分（该维度实得/该维度满分×100）→ 总分（Σ 维度分×权重，权重归一化）。
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.core import rules
from app.core.errors import BizError, ErrCode

logger = logging.getLogger("ScoringEngine")

# 内置兜底阈值（百分制，从高到低匹配）
DEFAULT_THRESHOLDS: List[Dict[str, Any]] = [
    {"min": 80, "level": "excellent", "name": "优秀"},
    {"min": 60, "level": "good", "name": "良好"},
    {"min": 0, "level": "needs_improvement", "name": "待改善"},
]
DEFAULT_SCORING_VERSION = "v0-placeholder"


def _read_config(key: str) -> Optional[str]:
    """从 system_config 读取配置值；任何异常都返回 None（走默认）。"""
    try:
        from app.utils.db import get_session
        from app.models.mysql.platform import SystemConfig

        session = get_session()
        try:
            row = session.query(SystemConfig).filter_by(config_key=key).first()
            return row.config_value if row else None
        finally:
            session.close()
    except Exception as e:  # 数据库不可用 / 表未建
        logger.warning(f"读取配置 {key} 失败，使用内置默认：{e}")
        return None


def _parse_json(raw: Optional[str]) -> Optional[Any]:
    if not raw:
        return None
    try:
        import json
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"配置项 JSON 解析失败，忽略：{e}")
        return None


class ScoringEngine:
    """确定性评分：输入模板配置 + 答案，输出结构化评分结果。"""

    # ------------------------------------------------------------------ #
    # 配置读取
    # ------------------------------------------------------------------ #
    @staticmethod
    def get_scoring_version() -> str:
        raw = _read_config("scoring.version")
        return (raw or DEFAULT_SCORING_VERSION).strip()

    @staticmethod
    def get_thresholds() -> List[Dict[str, Any]]:
        """等级阈值表，按 min 降序；非法配置回退默认。"""
        cfg = _parse_json(_read_config("scoring.thresholds"))
        if isinstance(cfg, list) and cfg and all(isinstance(x, dict) and "min" in x for x in cfg):
            return sorted(cfg, key=lambda x: float(x["min"]), reverse=True)
        return DEFAULT_THRESHOLDS

    @staticmethod
    def get_weights(template_code: Optional[str],
                    dimensions: List[Dict[str, Any]]) -> Tuple[Dict[str, float], str]:
        """维度权重：优先 system_config → 模板自带 weight → 等权。

        返回值：(已完成归一化的权重字典, 来源标识)。
        权重和一定为 1，保证加权总分落在百分制内。

        注意：来源通过返回值传递而非类属性——类属性会被并发请求互相覆盖。
        """
        keys = [d.get("key") for d in dimensions if d.get("key")]
        if not keys:
            return {}

        cfg = _parse_json(_read_config("scoring.weights")) or {}
        raw: Dict[str, float] = {}
        source = "template"

        if isinstance(cfg, dict):
            if template_code and isinstance(cfg.get(template_code), dict):
                raw = dict(cfg[template_code])
                source = "config"
            elif isinstance(cfg.get("default"), dict):
                raw = dict(cfg["default"])
                source = "config"

        if not raw:
            # 回退：模板自带权重
            raw = {k: float(d.get("weight") or 0)
                   for k, d in zip(keys, dimensions) if d.get("weight")}
            source = "default_equal" if not any(raw.values()) else "template"

        # 只保留模板声明过的维度，缺失维度补 0 后再等权兜底
        weights = {k: float(raw.get(k, 0) or 0) for k in keys}
        total = sum(weights.values())
        if total <= 0:
            weights = {k: 1.0 / len(keys) for k in keys}
            source = "default_equal"
        else:
            weights = {k: v / total for k, v in weights.items()}

        # 返回来源而非写入类属性：类属性在并发请求下会互相覆盖
        return weights, source

    # ------------------------------------------------------------------ #
    # 核心算分
    # ------------------------------------------------------------------ #
    @staticmethod
    def normalize_answers(answers: Any) -> Dict[str, Any]:
        """兼容两种答案结构：{"q1": 3} 或 [{"question_id":"q1","value":3}]。"""
        out: Dict[str, Any] = {}
        if isinstance(answers, dict):
            for k, v in answers.items():
                if isinstance(v, dict) and "value" in v:
                    out[str(k)] = v.get("value")
                else:
                    out[str(k)] = v
        elif isinstance(answers, list):
            for item in answers:
                if isinstance(item, dict):
                    qid = item.get("question_id") or item.get("id")
                    if qid is not None:
                        out[str(qid)] = item.get("value")
        return out

    @staticmethod
    def _question_max_score(q: Dict[str, Any]) -> float:
        """题目满分：优先 max_score，否则取选项最高分，再兜底 1。"""
        if q.get("max_score") is not None:
            try:
                return float(q["max_score"])
            except (TypeError, ValueError):
                pass
        scores = [float(o.get("score", 0)) for o in (q.get("options") or []) if isinstance(o, dict)]
        return max(scores) if scores else 1.0

    @staticmethod
    def _question_earned(q: Dict[str, Any], value: Any) -> float:
        """单题实得分。

        题型：
          - single：选项匹配取 score
          - multi ：选中项 score 求和（上限满分）
          - number：数值本身（上限满分，负值按 0）
          - 其他/文本：0 分（不参与）
        reverse=True 时反向计分：满分 - 实得。
        """
        if value is None:
            return 0.0
        qtype = (q.get("type") or "single").lower()
        max_score = ScoringEngine._question_max_score(q)
        options = q.get("options") or []

        if qtype == "single":
            got = 0.0
            for o in options:
                if isinstance(o, dict) and str(o.get("value")) == str(value):
                    got = float(o.get("score", 0) or 0)
                    break
            else:
                # 无选项定义时，若答案本身是数值则按数值计
                try:
                    got = float(value)
                except (TypeError, ValueError):
                    got = 0.0
        elif qtype == "multi":
            picked = value if isinstance(value, list) else [value]
            score_map = {str(o.get("value")): float(o.get("score", 0) or 0)
                         for o in options if isinstance(o, dict)}
            got = 0.0
            for p in picked:
                if p in score_map:
                    got += score_map[p]
                else:
                    try:
                        got += float(p)
                    except (TypeError, ValueError):
                        pass
            got = min(got, max_score)
        elif qtype == "number":
            try:
                got = float(value)
            except (TypeError, ValueError):
                got = 0.0
            got = min(max(got, 0.0), max_score)
        else:
            return 0.0

        got = min(max(got, 0.0), max_score)
        if q.get("reverse"):
            got = max_score - got
        return got

    @staticmethod
    def score(template: Any, answers: Any) -> Dict[str, Any]:
        """计算总分与维度分。

        :param template: AssessmentTemplate 实例或含 dimension_config/question_config 的对象
        :param answers: 答案（dict 或 list，见 normalize_answers）
        :return: {score, standardized_score, level, level_name, dimension_scores,
                  suggestions, scoring_version, config_source}
        """
        if template is None:
            raise BizError(ErrCode.TEMPLATE_NOT_FOUND, "模板不存在")

        dims = template.dimension_config or []
        questions = template.question_config or []
        if not isinstance(dims, list) or not dims:
            raise BizError(ErrCode.SCORE_ENGINE_ERROR, "模板缺少维度配置 dimension_config")

        ans = ScoringEngine.normalize_answers(answers)
        template_code = getattr(template, "code", None)
        weights, weight_source = ScoringEngine.get_weights(template_code, dims)

        # BV-18：模板声明的维度权重合计应为 1。不合规不阻断评分（引擎已归一化兜底），
        # 但把告警回传，供管理端在模板审核页提示营养师修正。
        declared = [d.get("weight") for d in dims if d.get("weight") is not None]
        config_warnings: List[str] = []
        if declared:
            wr = rules.check_weights(declared)
            if not wr.ok or wr.level == "warn":
                config_warnings.append(wr.message)

        # 按维度聚合实得 / 满分
        earned: Dict[str, float] = {}
        full: Dict[str, float] = {}
        dim_name: Dict[str, str] = {}
        for d in dims:
            key = str(d.get("key"))
            dim_name[key] = d.get("name") or key
            earned.setdefault(key, 0.0)
            full.setdefault(key, 0.0)
        for q in questions:
            if not isinstance(q, dict):
                continue
            qid = str(q.get("question_id") or q.get("id") or "")
            dim = str(q.get("dimension") or "")
            if dim not in full:
                # 题目声明了未知维度：忽略，避免污染总分
                continue
            full[dim] += ScoringEngine._question_max_score(q)
            if qid and qid in ans:
                earned[dim] += ScoringEngine._question_earned(q, ans[qid])

        thresholds = ScoringEngine.get_thresholds()
        dimension_scores: List[Dict[str, Any]] = []
        total = 0.0
        for d in dims:
            key = str(d.get("key"))
            f = full.get(key, 0.0)
            e = earned.get(key, 0.0)
            pct = round(e / f * 100, 2) if f > 0 else 0.0
            lvl = ScoringEngine.level_of(pct, thresholds)
            dimension_scores.append({
                "key": key,
                "name": dim_name.get(key, key),
                "score": pct,
                "level": lvl["level"],
                "level_name": lvl["name"],
                "weight": round(weights.get(key, 0.0), 4),
            })
            total += pct * weights.get(key, 0.0)

        total = round(min(max(total, 0.0), 100.0), 2)
        level = ScoringEngine.level_of(total, thresholds)

        return {
            "score": total,
            "standardized_score": total,  # 本引擎即百分制，跨模板对比无需换算
            "level": level["level"],
            "level_name": level["name"],
            "dimension_scores": dimension_scores,
            "suggestions": ScoringEngine.build_suggestions(dimension_scores),
            "scoring_version": ScoringEngine.get_scoring_version(),
            "config_source": {
                "weights": weight_source,
                "thresholds": "config" if _read_config("scoring.thresholds") else "default",
            },
            "config_warnings": config_warnings,
        }

    @staticmethod
    def level_of(score: float, thresholds: Optional[List[Dict[str, Any]]] = None) -> Dict[str, str]:
        """按阈值表判定等级（降序首个满足 min 的档位）。"""
        for t in (thresholds or ScoringEngine.get_thresholds()):
            if score >= float(t.get("min", 0)):
                return {"level": t.get("level", "good"), "name": t.get("name", "")}
        last = (thresholds or DEFAULT_THRESHOLDS)[-1]
        return {"level": last.get("level", "needs_improvement"), "name": last.get("name", "待改善")}

    @staticmethod
    def build_suggestions(dimension_scores: List[Dict[str, Any]], top_n: int = 2) -> List[Dict[str, Any]]:
        """基于最低维度生成结构化建议（规则计算，非 LLM）。

        结构化字段是营养师筛选、趋势图、统计分析的共同依赖（需求 C4）。
        具体食材与增量数值待营养师顾问产出规则库后改为配置驱动。
        """
        ranked = sorted(dimension_scores, key=lambda x: x.get("score", 0))
        out: List[Dict[str, Any]] = []
        for d in ranked[:top_n]:
            if d.get("score", 100) >= 80:
                continue
            out.append({
                "type": "dimension",
                "item": d.get("name") or d.get("key"),
                "dimension_key": d.get("key"),
                "score": d.get("score"),
                "delta": None,
                "unit": None,
                "frequency": "每日",
                "note": f"该维度得分 {d.get('score')}（{d.get('level_name')}），"
                        f"建议优先改善；具体食材与增量待营养师规则库配置",
            })
        if not out:
            out.append({
                "type": "dimension",
                "item": "总体保持",
                "dimension_key": None,
                "score": None,
                "delta": None,
                "unit": None,
                "frequency": "每日",
                "note": "各维度均在良好及以上，建议保持现有饮食与作息习惯",
            })
        return out


scoring_engine = ScoringEngine()
