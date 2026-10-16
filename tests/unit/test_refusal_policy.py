# -*- coding: utf-8 -*-
"""合规拒答策略边界测试（2026-10-16 口径）。

口径（需求 V1.3 D4 / 需求评审 D4 / 用例 IT-C-07~10）：
  - 用药·剂量 / 疾病诊断 / 检查报告解读 / 孕产妇与婴幼儿特殊人群 —— 一律拒答 + 引导就医；
  - 但**裸病名必须放行**："糖尿病主食怎么选""高血压能不能吃咸菜"是本平台核心场景，
    拒答应只针对"替我判断有没有得病"这类诊断诉求，不能连食养问答一起拒掉。
"""


def test_medical_and_diagnosis_refused():
    from app.core.refusal import check_refusal
    for q, reason in [("这个药剂量多少", "medical_advice"),
                      ("降压药怎么停", "medical_advice"),
                      ("我该吃多少降压药", "medical_advice"),      # 口语化剂量（SM-16）
                      ("我是不是得了糖尿病", "diagnosis"),
                      ("我是不是糖尿病", "diagnosis"),             # 口语化诊断（IT-C-08）
                      ("帮我看下报告单", "report_interpretation"),
                      ("帮我解读一下这个化验单", "report_interpretation")]:  # IT-C-09
        refused, why = check_refusal(q)
        assert refused and why == reason, (q, refused, why)


def test_general_diet_questions_allowed():
    from app.core.refusal import check_refusal
    for q in ["核桃一天吃几个合适", "糖尿病主食怎么选", "高血压人群能吃咸菜吗",
              "山药有什么功效", "膳食纤维每天吃多少", "你好"]:
        refused, why = check_refusal(q)
        assert not refused, (q, why)


def test_pregnancy_population_refused():
    """孕婴特殊人群整类拒答（2026-10-16 恢复；此前被自行收窄为条件拒答）。"""
    from app.core.refusal import check_refusal, refusal_message
    for q in ["孕妇能吃这个吗", "孕妇能吃山楂吗", "哺乳期能喝菊花茶吗",
              "宝宝辅食能加山药吗", "孕妇感冒吃什么药"]:
        refused, why = check_refusal(q)
        assert refused, q
        # 断言"引导就医"这个**语义契约**，不钉死某一个词。
        # 四类拒答的文案分两套：孕婴类是"请由产科或儿科医师评估"，其余三类是
        # "建议咨询医生或前往正规医疗机构就诊"——都满足 D4 的"引导就医"要求，
        # 但只含"医生"不含"医师"。早先这里写死断言"医师"，于是"孕妇感冒吃什么药"
        # （命中用药类、优先级高于孕婴类）被误判为不满足要求。
        # 合规要看的是有没有给出就医指引，不是用哪个称谓。
        msg = refusal_message(why)
        assert any(hint in msg for hint in
                   ("医生", "医师", "医疗机构", "就医", "就诊", "遵医嘱")), (q, why, msg)


def test_smalltalk_not_refused():
    from app.core.refusal import check_refusal
    for q in ["你好", "谢谢", "再见", ""]:
        assert not check_refusal(q)[0]
