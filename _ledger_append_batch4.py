# -*- coding: utf-8 -*-
"""向台账**追加**批次 4 与 P2 的用例行（2026-10-16）。

与 `_ledger_backfill.py` 的区别：那个只**回填既有行**，本脚本是**新增行**——
批次 4（邮箱注册 / AI 咨询辅助建议 / 运营指标）与 P2 技术债守卫是既有 216 条之外的新增能力，
台账里原本没有对应编号，只能追加。

四条纪律（与既有回填口径一致）：
1. 只写**有实测依据**的行：每条都给到测试文件::函数名，且在本次回归中实测通过；
2. 编号前缀 `B4-` / `P2-` 与既有 SM/IT/BV 段**不冲突**，便于区分「新增能力」与「原型验收」；
3. 写前备份（`_ledger_util.dump` 已内建，产出 `.bak.csv`）；
4. 列结构与既有台账完全一致，不做自定义列。
"""
from __future__ import annotations

import sys

sys.path.insert(0, "D:/AI_health")

from _ledger_util import dump, load  # noqa: E402

EMAIL = "tests/integration/test_email_auth.py"
SUG = "tests/integration/test_ai_suggestion_cases.py"
OPS = "tests/integration/test_ops_metrics_cases.py"
HYG = "tests/unit/test_code_hygiene.py"

# (编号, 子模块, 标题, 优先级, 实际结果, 备注(依据), 自动化证据)
ROWS = [
    # ---------------- 批次 4-1 邮箱注册（21 条，含参数化） ----------------
    ("B4-01", "邮箱注册", "邮箱格式校验（6 种非法输入均拒绝）", "P0",
     "6 种非法邮箱（空、全空格、缺 @、缺域名、含空格、双点/点开头域名）全部 40001 拒绝",
     "app/core/rules.check_email；参数化 6 例", f"{EMAIL}::test_email_format_rejected"),
    ("B4-02", "邮箱注册", "邮箱加密、哈希与掩码", "P0",
     "同一邮箱加密/哈希可复现；掩码形如 a***@example.invalid，不泄露原文",
     "app/utils/security：_email_keys / email_hash / encrypt_email / mask_email",
     f"{EMAIL}::test_email_encrypt_hash_and_mask"),
    ("B4-03", "邮箱注册", "演示模式返回 demo_code", "P1",
     "MAIL_DEMO_MODE=true 时返回 demo_code，便于本地联调",
     "ENV != production 且 MAIL_DEMO_MODE=true", f"{EMAIL}::test_email_send_demo_mode"),
    ("B4-04", "邮箱注册", "非法邮箱地址拒发", "P1",
     "格式非法时 40001，不落到「已发送」",
     "端点入参先过 check_email", f"{EMAIL}::test_email_send_rejects_bad_address"),
    ("B4-05", "邮箱注册", "60s 重发限频", "P0",
     "60s 内二次发码返回 60005 EMAIL_RATE_LIMITED",
     "EMAIL_RESEND_INTERVAL=60；ErrCode.EMAIL_RATE_LIMITED", f"{EMAIL}::test_email_rate_limit"),
    ("B4-06", "邮箱注册", "生产环境绝不返回 demo_code", "P0",
     "ENV=production 且未配 SMTP 时返回 90000 SYSTEM_ERROR，不伪造发送成功",
     "与短信 EX-01 同口径：网关没配属「服务端未就绪」，语义与「本次发送失败」不同",
     f"{EMAIL}::test_email_send_never_fakes_success_in_production"),
    ("B4-07", "邮箱注册", "验证码错误计次与一次性失效", "P0",
     "连续错 5 次锁定；校验成功后立即失效，不能复用",
     "EMAIL_CODE_MAX_ATTEMPTS=5", f"{EMAIL}::test_email_code_service_wrong_attempts_and_once_only"),
    ("B4-08", "邮箱注册", "未发送/已过期的验证码", "P1",
     "未申请或已过期均判定失败，不出现「无码却通过」",
     "缓存 TTL=EMAIL_CODE_TTL(300s)", f"{EMAIL}::test_email_code_expired_or_never_sent"),
    ("B4-09", "邮箱注册", "邮箱注册 + 邮箱登录闭环", "P0",
     "注册成功后可凭邮箱登录；返回体含 access_token",
     "auth_router 三通道；user_service.register_by_email", f"{EMAIL}::test_register_and_login_by_email"),
    ("B4-10", "邮箱注册", "邮箱重复与大小写归一", "P0",
     "Alice@Example.com 与 alice@example.com 视为同一账号，重复注册被拒",
     "normalize_email 先 strip+lower 再哈希", f"{EMAIL}::test_register_email_duplicate_and_case_insensitive"),
    ("B4-11", "邮箱注册", "邮箱须与验证码成对出现", "P1",
     "只给 email 不给 email_code（或反之）→ 40001",
     "RegisterReq validator 成对校验", f"{EMAIL}::test_register_email_requires_code_pair"),
    ("B4-12", "邮箱注册", "三通道互斥", "P0",
     "username / phone / email 三选一，同时给两个 → 40001",
     "注册通道互斥校验", f"{EMAIL}::test_register_channels_are_mutually_exclusive"),
    ("B4-13", "邮箱注册", "邮箱账号不占用手机号", "P1",
     "邮箱注册的账号 phone_cipher 为空，不伪造手机号",
     "sys_user.phone_cipher IS NULL", f"{EMAIL}::test_email_account_does_not_have_phone"),

    # ---------------- 批次 4-2 AI 咨询辅助建议（13 条） ----------------
    ("B4-14", "AI 咨询辅助", "普通用户无权调用", "P0",
     "普通用户调用 → 10005 PERMISSION_DENIED",
     "require_approved_dietitian", f"{SUG}::test_suggestion_denied_for_plain_user"),
    ("B4-15", "AI 咨询辅助", "未审核营养师被拒", "P0",
     "仅有 dietitian 角色、资质未审核 → 30001 DIETITIAN_NOT_FOUND",
     "审核闸门不可靠 role 绕过", f"{SUG}::test_suggestion_denied_for_unapproved_dietitian"),
    ("B4-16", "AI 咨询辅助", "非接管营养师被拒", "P0",
     "非本次咨询当事人 → 90002 RESOURCE_NOT_FOUND（防枚举，不区分「不是你的」与「不存在」）",
     "评审 M3 / SEC-04 口径", f"{SUG}::test_suggestion_denied_for_other_dietitian"),
    ("B4-17", "AI 咨询辅助", "未接入时统一 90002", "P1",
     "未 accept 的营养师视角为 90002「咨询不存在」，不暴露咨询 ID 是否存在",
     "防枚举口径", f"{SUG}::test_suggestion_rejected_when_not_accepted"),
    ("B4-18", "AI 咨询辅助", "管理员视角可见 90001", "P1",
     "管理员是当事人，可走到「状态未接入」分支 → 90001 且提示先接入",
     "同一闸门对当事人与外人给不同粒度", f"{SUG}::test_suggestion_for_pending_seen_by_admin"),
    ("B4-19", "AI 咨询辅助", "已结束咨询不再生成建议", "P1",
     "closed 状态 → 90001 并提示已结束",
     "避免给已关闭会话补建议后发送失败", f"{SUG}::test_suggestion_rejected_when_closed"),
    ("B4-20", "AI 咨询辅助", "高风险诉求拒答且不调用模型", "P0",
     "用药剂量类诉求 → refused=true / state=refused；注入「一调就炸」的假模型证明模型未被调用",
     "边界划在模型之前；合规看有无就医指引", f"{SUG}::test_suggestion_refuses_high_risk_without_calling_model"),
    ("B4-21", "AI 咨询辅助", "孕产妇整类拒答", "P0",
     "孕期饮食咨询 → refused=true",
     "需求 V1.3 D4 特殊人群", f"{SUG}::test_suggestion_refuses_pregnancy_diet"),
    ("B4-22", "AI 咨询辅助", "正常路径与 prompt 组装", "P0",
     "假模型返回草稿；引用带 doc_id 可溯源；prompt 含【客户健康档案】【咨询对话】【知识库片段】【营养师补充要点】；草稿不落消息表",
     "A7-4 来源可溯源；AI 绝不自动发给客户", f"{SUG}::test_suggestion_ok_and_prompt_assembly"),
    ("B4-23", "AI 咨询辅助", "模型不可用如实降级", "P0",
     "state=basic + 检索片段 + 明确提示，不编造建议",
     "None 表示不可用（非空串），逼调用方降级", f"{SUG}::test_suggestion_degrades_honestly_without_model"),
    ("B4-24", "AI 咨询辅助", "真实链路形状与状态取值", "P1",
     "不注入假模型：state ∈ {ok,basic,refused}；若 state=ok 则内容必非空",
     "不允许「state=ok 但内容为空」这种假装成功", f"{SUG}::test_suggestion_real_link_shape"),
    ("B4-25", "AI 咨询辅助", "ai_assisted 只对营养师生效", "P0",
     "营养师传 true → 落库 1；客户传 true → 仍为 0（防串标污染 ai_ratio）",
     "consultation_message.ai_assisted 语义边界", f"{SUG}::test_ai_assisted_flag_only_for_dietitian"),
    ("B4-26", "AI 咨询辅助", "has_open 枚举取值回归", "P0",
     "active（已接入）必须算作「未结束」；原实现误写 assessment_status 的 in_progress → 已修",
     "consultation_status 合法域 pending/active/closed/timeout", f"{SUG}::test_has_open_counts_active_consultation"),

    # ---------------- 批次 4-3 运营指标（7 条） ----------------
    ("B4-27", "运营指标", "运营大屏权限边界", "P0",
     "普通用户 10005；未登录 10001",
     "require_role(ADMIN, OPERATOR)", f"{OPS}::test_ops_dashboard_requires_admin"),
    ("B4-28", "运营指标", "指标齐备与内部自洽", "P0",
     "dau/mau/month_assessment/ai_ratio/template_usage 齐备且类型正确；dau ≤ mau ≤ users；占比之和≈1",
     "《设计_01》§五 指标口径", f"{OPS}::test_ops_metrics_shape_and_consistency"),
    ("B4-29", "运营指标", "活跃口径不只认登录", "P0",
     "造一个从不登录、只提交测评的用户，DAU 必须 +1",
     "last_login_at 会系统性少算「登录一次用一下午」的人", f"{OPS}::test_dau_counts_user_who_never_logged_in"),
    ("B4-30", "运营指标", "多来源活跃不重复计数", "P0",
     "同一用户既提测评又发起咨询，仍只算 1 个活跃用户",
     "四路 UNION 自带去重", f"{OPS}::test_dau_not_double_counted_across_sources"),
    ("B4-31", "运营指标", "ai_ratio 分母只含营养师消息", "P0",
     "营养师发 2 条（1 条 AI 辅助）+ 客户发 2 条 → 计数增量恰为 (+1/+2)",
     "客户消息进分母会把比例压扁", f"{OPS}::test_ai_ratio_counts_only_dietitian_messages"),
    ("B4-32", "运营指标", "模板使用率归属正确", "P1",
     "新建模板提交 1 次 → 该模板计数=1、share>0，且其他模板计数不变",
     "增量正确，不搬别人的记录", f"{OPS}::test_template_usage_attributes_records"),
    ("B4-33", "运营指标", "无归属记录不进分母", "P1",
     "构造一条不属于任何模板的记录，占比之和仍恒为 1",
     "否则指标退化成一个没人能解释的数", f"{OPS}::test_template_usage_denominator_excludes_unattributed"),

    # ---------------- P2 技术债守卫（6 条） ----------------
    ("P2-01", "技术债守卫", "交付代码无裸 except", "P0",
     "app/ 与 main.py 内裸 except 为 0（历史 5 处全在 legacy_nrs2002/ 隔离目录）",
     "裸 except 会连 KeyboardInterrupt/SystemExit 一起吞", f"{HYG}::test_shipping_code_has_no_bare_except"),
    ("P2-02", "技术债守卫", "服务端代码无 print", "P1",
     "app/ 内 print() 为 0（scripts/ 与根目录 _e2e_*.py 豁免：那是命令行输出）",
     "服务端日志一律走 logging，否则绕过日志级别与采集", f"{HYG}::test_shipping_code_has_no_print"),
    ("P2-03", "技术债守卫", "app 下无空目录", "P2",
     "app/ 下空目录为 0",
     "骨架残留会伪装成「有模块」", f"{HYG}::test_no_empty_package_dirs"),
    ("P2-04", "技术债守卫", "配置键必须有消费方", "P1",
     "config.py 声明的键全部有人读（本轮删掉 7 个全仓零消费的键）",
     "体检 P2-5：MILVUS_URI 与一批 Django 残留恒为 None", f"{HYG}::test_every_config_key_has_a_consumer"),
    ("P2-05", "技术债守卫", "预留键必须写明理由", "P2",
     "RESERVED_CONFIG_KEYS 中每个键在 config.py 都有说明注释",
     "防止豁免名单变成「让用例变绿」的后门", f"{HYG}::test_reserved_config_keys_are_documented"),
    ("P2-06", "技术债守卫", ".env 与 .env.example 键一致", "P1",
     "两文件键集合完全相同",
     "缺失即「新人照抄也起不来」", f"{HYG}::test_env_and_example_have_same_keys"),
]


def main() -> int:
    rows = load()
    cols = list(rows[0].keys())
    existing = {r["用例编号"] for r in rows}

    added, skipped = [], []
    for no, sub, title, pri, result, basis, ev in ROWS:
        if no in existing:
            skipped.append(no)
            continue
        added.append({
            "用例编号": no, "所属模块": "批次4/技术债（2026-10-16 新增）", "子模块": sub,
            "用例标题": title, "优先级": pri, "用例类型": "B4" if no.startswith("B4") else "P2",
            "执行状态": "已执行（自动化）", "实际结果": result, "备注(依据)": basis,
            "自动化覆盖": "已覆盖", "自动化证据": ev,
        })

    if not added:
        print("无新增行（可能已追加过）")
        return 0

    rows.extend(added)
    dump(rows)                       # 内部先备份为 .bak.csv
    print(f"追加 {len(added)} 行；跳过已存在 {len(skipped)} 行；台账共 {len(rows)} 行")
    print(f"列一致性：{list(added[0].keys()) == cols}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
