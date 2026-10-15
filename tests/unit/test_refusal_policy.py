# -*- coding: utf-8 -*-
"""合规拒答策略边界测试（2026-09-11 调整后的口径）。

口径：用药/剂量/诊断/报告解读一律拒答；孕婴类仅在与医疗干预词同时出现时拒答，
一般性饮食常识（如"孕妇能吃山楂吗"）放行——知识库有"慎用+咨询医师"标准口径。
"""


def test_medical_and_diagnosis_refused():
    from app.core.refusal import check_refusal
    for q, reason in [("这个药剂量多少", "medical_advice"),
                      ("降压药怎么停", "medical_advice"),
                      ("我是不是得了糖尿病", "diagnosis"),
                      ("帮我看下报告单", "report_interpretation")]:
        refused, why = check_refusal(q)
        assert refused and why == reason, (q, refused, why)

def test_general_diet_questions_allowed():
    from app.core.refusal import check_refusal
    for q in ["孕妇能吃山楂吗", "哺乳期能喝菊花茶吗", "核桃一天吃几个合适",
              "糖尿病主食怎么选", "你好"]:
        refused, why = check_refusal(q)
        assert not refused, (q, why)


def test_pregnancy_refused_only_with_medical_intent():
    from app.core.refusal import check_refusal
    # 孕婴 + 医疗干预词：拒答（命中优先级更高的 medical_advice 也算拒答）
    for q in ["孕妇感冒吃什么药", "宝宝拉肚子需要打针吗", "孕妇能不能吃保胎药"]:
        refused, _ = check_refusal(q)
        assert refused, q
    # 一般性饮食常识：放行
    for q in ["孕妇能吃山楂吗", "哺乳期能喝菊花茶吗", "宝宝辅食能加山药吗"]:
        assert not check_refusal(q)[0], q


def test_smalltalk_not_refused():
    from app.core.refusal import check_refusal
    for q in ["你好", "谢谢", "再见", ""]:
        assert not check_refusal(q)[0]
