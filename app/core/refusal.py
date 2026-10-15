# -*- coding: utf-8 -*-
"""合规拒答策略（设计基线：命中即不调用模型，直接返回标准拒答）。

依据需求合规 D4 / 接口设计 §3.5。仅做关键词粗筛，命中后由前端展示标准话术，
绝不进入 LLM，避免生成用药/诊断等高风险内容。

2026-09-11 调整：孕婴类不再一刀切——"孕妇能吃山楂吗"这类一般性饮食常识问题
放行（知识库有"慎用+咨询医师"的标准口径），仅当孕婴词与医疗干预词同时出现
（如"孕妇感冒吃什么药"）才拒答。
"""
from typing import Tuple

# 各拒答类别触发词（可后续接入 system_config 配置化）
_REFUSE_RULES = {
    "medical_advice": ["吃多少粒", "剂量", "用量", "开药", "处方", "停药", "换药",
                       "怎么吃药", "吃什么药", "怎么停", "能否停"],
    "diagnosis": ["确诊", "是不是得了", "诊断", "是什么病", "严重吗", "会不会恶化"],
    "report_interpretation": ["报告单", "化验单", "检查报告", "帮我看下报告"],
}

# 孕婴词 + 医疗干预词 同时出现才拒答
_PREG_KEYWORDS = ["孕妇", "怀孕", "哺乳期", "妊娠", "婴幼儿", "婴儿", "胎儿", "宝宝"]
# 注意：不能用裸"药"字——山药、药膳、芍药等食药同源名称含"药"会误伤
_PREG_RISK = ["吃药", "用药", "药物", "服药", "中药", "停药", "换药", "什么药",
              "治疗", "流产", "出血", "见红", "腹痛", "保胎",
              "打针", "针剂", "输液", "疫苗", "手术"]


def _pregnancy_refuse(t: str) -> bool:
    has_group = any(k in t for k in _PREG_KEYWORDS)
    has_risk = any(k in t for k in _PREG_RISK)
    return has_group and has_risk


def check_refusal(text: str) -> Tuple[bool, str]:
    """返回 (是否拒答, 拒答原因)。

    优先级：用药 > 诊断 > 报告解读 > 孕婴医疗干预。
    """
    if not text:
        return False, ""
    t = text.lower()
    for reason, keywords in _REFUSE_RULES.items():
        for kw in keywords:
            if kw.lower() in t:
                return True, reason
    if _pregnancy_refuse(t):
        return True, "pregnancy"
    return False, ""
