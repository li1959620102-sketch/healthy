# -*- coding: utf-8 -*-
"""台账集中回填（第 38 项任务）。

只回填**有实测依据**的行。每条都要能说出"依据来自哪个文件/脚本/实测数字"，
拿不出依据的一律留在"未执行"，不为了好看把 153 条凑成 153 条通过。

依据来源分三类：
  A. pytest 自动化用例 —— 依据是测试函数名 + 本次回归结论（全绿/失败数）
  B. 浏览器 E2E          —— 依据是 Playwright 脚本的判定明细
  C. 真实服务性能实测    —— 依据是实测数字（首字节/总耗时 P95）

口径统一：HTTP 恒 200，业务结果看响应体 code，错误码以 app/core/errors.py 为准。
"""
from __future__ import annotations

import re
import sys
from typing import List

sys.path.insert(0, "D:/AI_health")

from _ledger_util import dump, fill, load  # noqa: E402

AUTH = "tests/integration/test_auth_user_cases.py"
DIET = "tests/integration/test_dietitian_template_cases.py"
ASR = "tests/integration/test_assessment_report_cases.py"
CHAT = "tests/integration/test_chat_consultation_cases.py"
DASH = "tests/integration/test_dashboard_admin_kb_cases.py"
SEC = "tests/integration/test_security_cases.py"
EXC = "tests/integration/test_exception_degrade_cases.py"
SMK = "tests/integration/test_smoke_cases.py"
BND = "tests/integration/test_boundary_cases.py"
PRF = "tests/integration/test_perf_ui_cases.py"


def a(fn, result, basis, src=None):
    """自动化用例行。

    `src` 缺省时按**测试函数名**推断所属文件——136 条 `a()` 调用遍布 8 个文件，
    逐条手写路径必然出错（实测已错：EX 段全指向了认证文件）。
    判定方式：函数名去掉 `test_` 后按段逐级加长去匹配 _FUNC_FILE
    （`test_it_n01_apply_then_gate` → 试 `it_n01_apply_then_gate`、`it_n01`、…、`it`）。
    段里的数字要先剥掉：`n01` 是 `n` 加序号，不是另一个模块。
    不中就落回 AUTH 并记入 _UNRESOLVED，回填结束时报警。
    """
    if src is None:
        parts = fn.split("_")
        body = parts[1:] if parts and parts[0] == "test" else parts
        # 参数化用例名带 [reason]，先去掉
        body = [re.sub(r"\d+$", "", p) for p in body]
        src = None
        for i in range(len(body), 0, -1):
            key = "_".join(body[:i])
            if key in _FUNC_FILE:
                src = _FUNC_FILE[key]
                break
        if src is None:
            src = AUTH
            _UNRESOLVED.append(fn)
    return {"执行状态": "已执行（自动化）", "实际结果": result,
            "备注(依据)": basis, "自动化覆盖": "已覆盖",
            "自动化证据": f"{src}::{fn}"}


_UNRESOLVED: List[str] = []

# 测试函数名段 → 所在文件。改文件名时只改这一处。
_FUNC_FILE = {
    "it_a": AUTH, "it_u": AUTH,
    "it_n": DIET, "it_t": DIET,
    "it_as": ASR, "it_r": ASR,
    "it_c": CHAT, "it_cs": CHAT,
    "it_d": DASH, "it_ad": DASH, "it_k": DASH,
    "sec": SEC, "sm": SMK, "ex": EXC,
}


MAPPING = {
    # ==================== IT-A 认证 ====================
    "IT-A-01": a("test_it_a01_sms_rate_limited", "同号 60 秒内二次发送被拒，返回 60003 并提示频率",
                 "ErrCode.SMS_RATE_LIMITED"),
    "IT-A-02": a("test_it_a02_wrong_verify_code_rejected", "错误验证码注册被拒；连错达上限后须重新获取",
                 "ErrCode.VERIFY_CODE_INVALID"),
    "IT-A-03": a("test_it_a03_expired_verify_code_rejected", "过期验证码被拒，不接受旧码",
                 "Redis 验证码 TTL"),
    "IT-A-04": a("test_it_a04_phone_already_registered", "已注册手机号再次注册被拒，不覆盖原账号",
                 "ErrCode.PHONE_EXISTS 系列"),
    "IT-A-05": a("test_it_a05_refresh_token_flow", "刷新令牌换新 access，旧 access 仍可用至过期",
                 "POST /auth/refresh"),
    "IT-A-06": a("test_it_a06_expired_access_token", "过期令牌访问受保护接口被拒",
                 "ErrCode.TOKEN_EXPIRED"),
    "IT-A-07": a("test_it_a07_idle_timeout_rejected", "超过空闲窗口的令牌被判定失效",
                 "令牌 idle 校验"),
    "IT-A-08": a("test_it_a08_logout_invalidates_token", "登出后原令牌立即失效（黑名单）",
                 "app/utils/token_blacklist.py"),
    "IT-A-10": a("test_it_a10_phone_change_requires_verify_code", "改绑手机号必须带短信验证码，缺失被拒",
                 "PUT /users/me/phone"),

    # ==================== IT-U 账号生命周期 ====================
    "IT-U-02": a("test_it_u02_sensitive_consent_required", "未单独同意敏感信息处理时，读取病史被拒",
                 "同意状态位校验"),
    "IT-U-03": a("test_it_u03_phone_change_takes_effect", "改绑后新号可登录、旧号登录失败",
                 "PUT /users/me/phone + 登录校验"),
    "IT-U-04": a("test_it_u04_password_change", "改密后旧密码失效、新密码可登录",
                 "PUT /users/me/password"),
    "IT-U-05": {"执行状态": "已执行（自动化）",
                "实际结果": "完整链路验证：造档案(171cm/63.5kg)+走完测评出报告→错误验证码被拒→"
                            "正确验证码导出 CSV/XLSX→按签名地址真实下载→文件内含本人 user_id 与真实身高体重、"
                            "测评与报告编号一一对应。过程中修掉 3 个真实缺陷："
                            "①_phone_of 查了 user_profile.phone（该列不存在，1054）；"
                            "② 引入不存在的 app.utils.crypto 导致解密静默失败、所有人收到「未绑定手机号」；"
                            "③ 声明 10 分钟失效实际签 7 天。",
                "备注(依据)": "POST /users/me/data-export；签名 X-Amz-Expires=600；"
                             "ErrCode 90001 未绑定手机号",
                "自动化覆盖": "已覆盖",
                "自动化证据": f"{AUTH}::test_it_u05_data_export_scope / "
                              "test_it_u05_data_export_real_file / test_it_u05_data_export_xlsx"},
    "IT-U-06": a("test_it_u06_login_after_deletion", "注销后登录被拒，并生成数据删除任务",
                 "sys_user.deleted_at + data_deletion_task"),
    "IT-U-07": a("test_it_u07_deletion_covers_three_stores", "注销登记覆盖业务库/向量库/对象存储三处",
                 "注销清理登记链"),
    "IT-U-08": a("test_it_u08_request_id_traceable", "成功与失败响应均带 request_id，可定位到日志",
                 "统一响应信封 request_id"),
    "IT-U-09": a("test_it_u09_concurrent_profile_update", "并发改资料无 500、无脏数据，最终值落在其中一次写入",
                 "并发写入实测"),

    # ==================== IT-N 营养师绑定 ====================
    "IT-N-01": a("test_it_n01_apply_then_gate", "未申请时调用营养师接口返回 30001；提交申请后放行",
                 "ErrCode.NOT_APPLIED(30001)"),
    "IT-N-02": a("test_it_n02_audit_rejected_resubmit", "驳回后可重新提交，状态机回到待审核",
                 "审核状态机"),
    "IT-N-03": a("test_it_n03_invite_code_binding", "凭邀请码绑定成功，建立绑定关系",
                 "邀请码绑定"),
    "IT-N-04": a("test_it_n04_only_one_dietitian_at_a_time", "同一客户同期只能绑 1 位营养师，换绑需先解绑",
                 "绑定唯一约束"),
    "IT-N-05": a("test_it_n05_auto_bind_after_consultation", "咨询结束后自动建立绑定",
                 "咨询→绑定联动"),
    "IT-N-06": a("test_it_n06_after_unbind_no_access", "解绑后营养师端立即看不到该客户",
                 "解绑后权限回收"),
    "IT-N-07": a("test_it_n07_customer_keeps_history_after_unbind", "解绑不删除客户侧历史记录",
                 "解绑≠注销"),
    "IT-N-08": a("test_it_n08_unbound_dietitian_cannot_see", "未绑定的营养师看不到该客户报告",
                 "越权拦截"),
    "IT-N-09": a("test_it_n09_customer_filter_by_level", "客户列表支持按风险等级筛选且结果正确",
                 "GET /nutritionists/customers"),
    "IT-N-10": a("test_it_n10_disabled_dietitian_loses_access", "营养师被禁用后立即失去访问能力",
                 "禁用即时生效"),
    "IT-N-11": a("test_it_n11_customer_side_unaffected", "营养师被禁用不影响客户自身侧使用",
                 "禁用影响面隔离"),
    "IT-N-12": a("test_it_n12_annotate_report", "营养师可对绑定客户报告写批注并保存",
                 "PUT /reports/{id}/annotation"),
    "IT-N-13": a("test_it_n13_annotation_requires_binding", "未绑定营养师批注被拒（90002）",
                 "ErrCode.NOT_BOUND(90002)"),

    # ==================== IT-T 模板状态机 ====================
    "IT-T-01": a("test_it_t01_template_state_machine", "草稿→待审核→已发布→已下线，非法跳转被拒",
                 "模板状态机"),
    "IT-T-02": a("test_it_t02_draft_pending_offline_invisible", "草稿/待审核/已下线模板对普通用户不可见",
                 "用户侧可见性过滤"),
    "IT-T-03": a("test_it_t03_edit_creates_new_version", "编辑已发布模板生成新版本号，旧版本保留",
                 "版本递增"),
    "IT-T-04": a("test_it_t04_version_locking", "按版本锁定测评记录，模板改动不影响进行中测评",
                 "template_version 快照"),
    "IT-T-05": a("test_it_t05_offline_keeps_history_blocks_new", "下线后历史报告仍可查，但不能再发起新测评",
                 "下线语义"),
    "IT-T-06": a("test_it_t06_offline_allows_in_progress", "模板下线不影响已开始的测评继续作答",
                 "进行中测评豁免"),
    "IT-T-08": a("test_it_t08_version_list", "版本列表按版本号倒序返回且与库中一致",
                 "GET /templates/{id}/versions"),
    "IT-T-09": a("test_it_t09_non_creator_cannot_edit", "非创建者编辑模板被拒",
                 "创建者权限校验"),
    "IT-T-10": a("test_it_t10_report_records_scoring_version", "报告记录模板版本与评分版本，可回溯当时口径",
                 "assessment_report.template_version/scoring_version"),

    # ==================== IT-AS 测评执行 ====================
    "IT-AS-03": a("test_it_as03_resume_on_another_device", "换设备打开同一测评恢复到上次草稿与进度",
                 "GET /assessments/{id} 恢复语义"),
    "IT-AS-05": a("test_it_as05_realtime_validation_warnings", "保存草稿返回实时校验告警（如未作答按 0 分计入）",
                 "PATCH /assessments/{id}/answers warnings"),
    "IT-AS-06": a("test_it_as06_required_answers_block_submit", "必填项缺失时提交被拒",
                 "必填校验"),
    "IT-AS-07": a("test_it_as07_save_draft_latency", "保存草稿实测在时延门槛内",
                 "保存草稿实测"),
    "IT-AS-08": a("test_it_as08_submit_latency", "提交测评实测在时延门槛内",
                 "提交实测"),
    "IT-AS-10": a("test_it_as10_idempotency_window_expired", "幂等窗口外的重复提交不被误判为同一次",
                 "Idempotency-Key 窗口"),
    "IT-AS-11": a("test_it_as11_missing_idempotency_key", "不带 Idempotency-Key 的提交被拒",
                 "接口层强制幂等键"),
    "IT-AS-12": a("test_it_as12_resubmit_completed_rejected", "已完成的测评重复提交被拒，不产生第二份报告",
                 "已提交状态校验"),
    "IT-AS-13": a("test_it_as13_reuse_in_progress", "同模板存在进行中测评时复用该记录而非新建",
                 "进行中复用"),
    "IT-AS-14": a("test_it_as14_cancel_then_submit_denied", "已作废（放弃）的测评再提交被拒",
                 "voided_at 校验"),
    "IT-AS-15": a("test_it_as15_guest_claim_transfer", "游客测评在注册后可通过认领转移到自己名下",
                 "POST /assessments/guest/claim"),
    "IT-AS-16": a("test_it_as16_claim_requires_sms_proof", "冒用他人游客凭证认领被拒（需手机号+验证码）",
                 "认领二次验证"),
    "IT-AS-18": a("test_it_as18_draft_optimistic_lock", "并发保存草稿时旧 version 被拒，不静默覆盖",
                 "saved_at 乐观锁"),

    # ==================== IT-R 报告 ====================
    "IT-R-01": a("test_it_r01_structured_fields", "报告含总分/标准化分/等级/维度分等结构化字段，类型正确",
                 "GET /reports/{id}"),
    "IT-R-02": a("test_it_r02_suggestion_shape", "建议项结构一致（内容+依据），可直接渲染",
                 "suggestions 形状"),
    "IT-R-03": a("test_it_r03_references_traceable", "报告引用带来源标识，可回溯到知识条目",
                 "references 字段"),
    "IT-R-04": a("test_it_r04_disclaimer", "报告固定包含免责声明",
                 "disclaimer 字段"),
    "IT-R-05": a("test_it_r05_generating_state", "生成中报告状态为 generating，前端可据此展示进度",
                 "assessment_report.state"),
    "IT-R-06": a("test_it_r06_retry_backoff", "生成失败自动重试且次数受限，不无限打上游",
                 "retry_count / 退避"),
    "IT-R-07": a("test_it_r07_degrade_to_basic", "模型不可用时降级为基础版（code=0 且 state=basic）",
                 "降级语义：不可用 50002"),
    "IT-R-08": a("test_it_r08_regenerate_keeps_report_id", "手动重生成沿用同一 report_id，不新增报告",
                 "重新生成幂等"),
    "IT-R-09": a("test_it_r09_answers_survive_fault", "生成过程中故障不影响已保存答案",
                 "答案持久化验证"),
    "IT-R-11": a("test_it_r11_stream_events_complete", "报告 SSE 事件齐全（start/delta/done），顺序正确",
                 "GET /reports/stream?ticket="),
    "IT-R-13": a("test_it_r13_annotation_marks_revised", "营养师批注后报告标记为已修订",
                 "annotation 状态位"),
    "IT-R-14": a("test_it_r14_other_user_report_404", "访问他人报告统一返回 404，不泄露存在性",
                 "水平越权防护"),

    # ==================== IT-C 智能查询 ====================
    "IT-C-01": {"执行状态": "已执行（自动化）",
                "实际结果": "统一预热后首字节进入稳态；缓存命中约 20~40ms，未命中路径经 SSE 分段实测为"
                            "检索 0.05s + 上游模型（裸调首 token 0.717s）。真实服务 47 条样本"
                            "首字节 P95=2.137s。",
                "备注(依据)": "POST /chat/query SSE；冷启动检索 7.9s→稳态 0.05s（预热含真实嵌入）",
                "自动化覆盖": "已覆盖",
                "自动化证据": f"{CHAT}::test_it_c01_c02_query_latency / test_it_c01b_ready_reports_agent_warmth"},
    "IT-C-02": {"执行状态": "已执行（自动化）",
                "实际结果": "真实服务 47 条不重复问题：完整回答 P95=12.474s、最大 14.445s；"
                            "上游裸调总耗时 9.366s，主要成本在第三方模型生成速度。",
                "备注(依据)": "SSE done 事件耗时实测；模型 Qwen/Qwen2.5-72B-Instruct",
                "自动化覆盖": "已覆盖",
                "自动化证据": f"{CHAT}::test_it_c01_c02_query_latency；_perf02_probe.py 实测"},
    "IT-C-03": a("test_it_c03_answer_cache", "相同问题二次提问命中缓存，done 事件标记 cached=true",
                 "答案缓存（仅缓存有依据且完整的回答）"),
    "IT-C-04": a("test_it_c04_short_input", "过短输入被识别为闲聊并给出引导话术，不空答",
                 "_smalltalk_kind 分流"),
    "IT-C-05": a("test_it_c05_reference_present", "回答附带引用片段，字段含标题与来源",
                 "reference 事件 docs"),
    "IT-C-06": a("test_it_c06_no_source_fallback", "知识库无相关片段时如实说明并给出替代路径，不编造",
                 "no_reference 兜底话术"),
    "IT-C-07": a("test_it_c07_10_refusal[medical_advice]", "用药剂量类问题拒答，且模型调用次数为 0",
                 "app/core/refusal.py + 反证模型未被调用"),
    "IT-C-08": a("test_it_c07_10_refusal[diagnosis]", "疾病诊断类问题拒答，模型未被调用",
                 "reason=diagnosis"),
    "IT-C-09": a("test_it_c07_10_refusal[report_interpretation]", "报告解读类问题拒答，模型未被调用",
                 "reason=report_interpretation"),
    "IT-C-10": a("test_it_c07_10_refusal[pregnancy]", "孕婴特殊人群问题拒答，模型未被调用",
                 "reason=pregnancy"),
    "IT-C-11": a("test_it_c11_multi_turn_context", "多轮追问带上文，检索词与回答均体现上下文",
                 "retrieval_query 拼接历史"),
    "IT-C-12": a("test_it_c12_sessions_and_history", "会话列表与历史消息可完整取回",
                 "GET /chat/sessions + /chat/sessions/latest"),
    "IT-C-13": a("test_it_c13_transfer_human", "可转人工，转接后进入人工咨询流程",
                 "转人工状态迁移"),
    "IT-C-14": a("test_it_c14_resume_after_human_closed", "人工结束后可恢复到智能体继续追问",
                 "转人工后恢复"),
    "IT-C-15": a("test_it_c15_busy_notice_without_dropping", "并发超容量时不拒绝不丢弃，回传负载提示",
                 "并发准入 enter/exit"),
    "IT-C-16": a("test_it_c16_model_error_event", "模型异常时推送 error 事件且不泄露堆栈",
                 "60001 服务繁忙"),

    # ==================== IT-CS 人工咨询 ====================
    "IT-CS-01": a("test_it_cs01_create_consultation", "发起咨询成功返回咨询 ID，状态为进行中",
                 "POST /consultations"),
    "IT-CS-02": a("test_it_cs02_status_machine", "咨询状态机（待接单/进行中/已结束）按规则迁移",
                 "咨询状态取值以实际枚举为准（active 而非 in_progress）"),
    "IT-CS-03": a("test_it_cs03_message_exchange", "客户与营养师双向收发消息成功",
                 "consultation_message 落库"),
    "IT-CS-04": a("test_it_cs04_non_party_404", "非相关方访问咨询返回 404，不泄露存在性",
                 "越权拦截"),
    "IT-CS-06": a("test_it_cs06_rating_once", "满意度只能评价一次，重复提交被拒",
                 "rating 一次性约束"),
    "IT-CS-07": a("test_it_cs07_close_blocks_send", "咨询结束后再发消息被拒",
                 "已结束咨询禁发"),
    "IT-CS-08": a("test_it_cs08_image_message_rejected", "图片消息当前不支持，如实拒绝而非静默丢弃",
                 "content_type 白名单"),

    # ==================== IT-D 看板 ====================
    "IT-D-01": a("test_it_d01_trend_same_template_only", "趋势只聚合同一模板的记录，跨模板不混算",
                 "GET /dashboard/trend"),
    "IT-D-02": a("test_it_d02_cross_template_annotated", "跨模板数据带模板标注，明确说明不可直接比较",
                 "template_name 标注"),
    "IT-D-03": a("test_it_d03_chart_latency", "图表查询实测在时延门槛内",
                 "看板查询实测"),
    "IT-D-04": a("test_it_d04_insight_latency", "智能解读实测在时延门槛内",
                 "解读耗时实测"),
    "IT-D-05": a("test_it_d05_attribution_rule_based", "归因基于规则而非模型推断，并列最大时如实说明并列",
                 "归因接口 + 并列口径"),
    "IT-D-07": a("test_it_d07_equivalent_table", "图表配有等价数据表，读屏与无图场景可用",
                 "等价表接口"),

    # ==================== IT-AD 管理后台 ====================
    "IT-AD-01": a("test_it_ad01_disable_user", "禁用用户后该用户令牌立即失效，且写审计日志",
                 "user.disable + 审计动作 user.disable"),
    "IT-AD-02": a("test_it_ad02_enable_user", "解封后用户可重新登录",
                 "user.enable"),
    "IT-AD-03": a("test_it_ad03_audit_list_admin_only", "资质审核列表仅管理员可见，普通用户被拒",
                 "角色权限校验"),
    "IT-AD-04": a("test_it_ad04_system_config_effective", "修改系统配置后接口行为随之改变",
                 "config.update"),
    "IT-AD-05": a("test_it_ad05_operation_log_fields", "操作日志字段齐全（动作/操作人/对象/时间/结果）",
                 "审计动作点分命名"),
    "IT-AD-06": a("test_it_ad06_overview_matches_db", "运营概览数字与库中实际统计一致（真实 total）",
                 "看板与 SQL 交叉核对"),
    "IT-AD-07": a("test_it_ad07_deep_paging", "深分页（第 3 页起）返回真实数据而非重复首页",
                 "page/page_size 真实生效"),

    # ==================== IT-K 知识库 ====================
    "IT-K-02": a("test_it_k02_embed_status_chain", "向量化状态按 pending→processing→done/failed 迁移并回写分块数",
                 "embed_status/chunk_count 维护"),
    "IT-K-03": a("test_it_k03_delete_syncs_vectors", "删除知识文档时向量优先删除，成功后再删元数据",
                 "向量与元数据联动"),
    "IT-K-04": a("test_it_k04_query_after_delete", "删除后问答检索不再命中该文档",
                 "删除后检索实测"),
    "IT-K-05": a("test_it_k05_non_admin_forbidden", "非管理员访问知识库管理接口被拒",
                 "角色权限校验"),

    # ==================== SEC 安全合规 ====================
    "SEC-01": a("test_sec01_cross_user_report_read_denied", "越权读取他人报告被拒",
                 "报告归属校验"),
    "SEC-02": a("test_sec02_cannot_modify_others_profile", "越权修改他人资料被拒",
                 "资料归属校验"),
    "SEC-03": a("test_sec03_non_bound_dietitian_cannot_annotate", "未绑定营养师批注被拒",
                 "ErrCode.NOT_BOUND(90002)"),
    "SEC-04": a("test_sec04_report_id_enumeration_uniform_404", "枚举他人 report_id 一律 404，响应体一致无差异",
                 "防水平越权枚举"),
    "SEC-07": a("test_sec07_tampered_jwt_rejected", "篡改签名的 JWT 被拒",
                 "JWT 签名校验"),
    "SEC-08": a("test_sec08_sse_token_not_in_url", "SSE 令牌不出现在 URL 中，改用 60 秒短票",
                 "?ticket= 短票机制"),
    "SEC-10": a("test_sec10_no_plaintext_phone_in_lists", "列表接口不返回明文手机号（脱敏或密文）",
                 "phone_cipher + 脱敏展示"),
    "SEC-11": a("test_sec11_disease_history_requires_consent", "未单独同意不返回病史等敏感个人信息",
                 "单独同意状态位"),
    "SEC-12": a("test_sec12_deletion_pipeline_clears_user_data", "注销流水线覆盖业务数据与向量、对象存储登记",
                 "三处存储清理登记"),
    "SEC-13": a("test_sec13_disclaimer_present", "AI 生成内容均带免责声明",
                 "disclaimer 字段"),
    "SEC-14": a("test_sec14_audit_log_immutable_to_users", "普通用户无法修改或删除审计日志",
                 "审计日志只读"),
    "SEC-15": a("test_sec15_https_redirect_in_production", "生产环境对 http 请求做 HTTPS 跳转",
                 "ENV=production 行为"),

    # ==================== SM 冒烟 ====================
    "SM-02": a("test_sm02_sms_code_issued", "验证码发送成功，演示模式回传 demo_code",
               "SMS_DEMO_MODE=true"),
    "SM-06": a("test_sm06_unauthenticated_blocked", "未登录访问受保护接口被拒",
               "get_current_user 依赖"),
    "SM-10": a("test_sm10_questions_and_empty_draft", "可取到题目列表，初始草稿为空",
               "GET /assessments/{id}"),
    "SM-16": a("test_sm16_refuse_medical_advice", "医疗建议类问题被拒答",
               "拒答策略"),
    "SM-19": a("test_sm19_dietitian_customer_list_only_bound", "营养师客户列表只含已绑定客户",
               "绑定关系过滤"),
    "SM-20": a("test_sm20_dietitian_audit_gate", "资质审核未通过前不进入工作台",
               "审核状态门禁"),
    "SM-23": a("test_sm23_personal_trend_ordered", "个人趋势按时间有序返回",
               "趋势排序"),
    "SM-25": a("test_sm25_account_deletion_task", "注销后生成数据删除任务并登记保留期",
               "data_deletion_task"),

    # ==================== EX 异常降级 ====================
    "EX-01": a("test_ex01_sms_unavailable", "短信网关不可用时如实提示，不伪造发送成功",
               "故障注入"),
    "EX-02": a("test_ex02_db_failure_no_stack", "数据库故障返回业务错误，不泄露堆栈",
               "统一异常处理 + SQLAlchemyError 捕获"),
    "EX-03": a("test_ex03_vector_store_unavailable", "向量库不可用时问答降级而非 500",
               "Milvus 故障注入"),
    "EX-04": a("test_ex04_model_timeout", "模型超时返回可读错误，用户侧有明确提示",
               "模型超时故障注入"),
    "EX-05": a("test_ex05_model_500_retries_then_degrade", "模型连续 500 重试到上限后降级",
               "重试上限 + 退避"),
    "EX-06": a("test_ex06_model_empty_no_blank_report", "模型返回空内容时不生成空白报告",
               "空响应防护"),
    "EX-07": a("test_ex07_junk_oversize_text", "乱码/超长输入被拒或安全截断，不打崩服务",
               "入参校验"),
    "EX-08": a("test_ex08_sse_interrupted_keeps_partial", "SSE 中断保留已产出部分，不整体丢弃",
               "流式中断处理"),
    "EX-09": a("test_ex09_refresh_does_not_regenerate", "前端刷新不触发报告重新生成",
               "报告复用"),
    "EX-10": a("test_ex10_degrade_state_semantics", "降级语义明确：code=0 且 state=basic，不返回 50002",
               "降级契约"),
    "EX-11": a("test_ex11_degraded_data_complete", "降级报告字段仍完整（结构不残缺）",
               "降级数据完整性"),
    "EX-12": a("test_ex12_regenerate_after_recovery", "上游恢复后可重新生成出完整报告",
               "恢复后重生成"),
    "EX-13": a("test_ex13_query_degrade_offers_fallback", "问答降级时提供检索片段作为兜底",
               "degraded 分支"),
    "EX-14": a("test_ex14_dashboard_degrade_deterministic", "看板降级结果确定，多次请求一致",
               "规则计算无随机"),
    "EX-15": a("test_ex15_answers_survive_faults", "多轮故障注入后用户答案不丢失",
               "答案持久化"),

    # ==================== BV 边界值 ====================
    "BV-09": {"执行状态": "已执行（自动化）",
              "实际结果": "三档全过：1 题模板可正常提交出报告；8 题模板完整作答；"
                          "200 题模板接口不超时、不截断，题目数与配置一致。",
              "备注(依据)": "模板 question_config 题数边界",
              "自动化覆盖": "已覆盖",
              "自动化证据": f"{BND}::test_bv09_single_question_template_can_submit / "
                            "test_bv09_eight_question_template / test_bv09_two_hundred_questions_not_degraded"},
    "BV-10": {"执行状态": "已执行（自动化）",
              "实际结果": "浏览器实测 8 项全过：8 题单页铺开且无分页控件；9 题自动分页为 8+1；"
                          "第 2 页仅渲染剩余 1 题；跨页进度累计显示 9/9；翻回第 1 页答案仍在；"
                          "草稿收到 9 题全量合并；零 JS 错误。实现中修掉一个真实缺陷："
                          "翻页时先改 AS.page 再收集答案，导致用新页切片读旧页 DOM，每次翻页丢一次作答。",
              "备注(依据)": "Playwright + Chromium 实测，单页上限 8 题",
              "自动化覆盖": "已覆盖",
              "自动化证据": "_e2e_quiz_paging.py（8/8 PASS）；frontend/assets/app.js renderQuizPage/renderQuizPager"},
    "BV-20": {"执行状态": "已执行（自动化）",
              "实际结果": "起>止 明确报错；起=止 含当日；非法格式报错。三种边界行为均符合预期。",
              "备注(依据)": "GET /dashboard/trend 时间范围校验",
              "自动化覆盖": "已覆盖",
              "自动化证据": f"{BND}::test_bv20_trend_date_range_rejects_inverted / "
                            "test_bv20_trend_bad_format_rejected / test_bv20_trend_same_day_inclusive"},
    "BV-26": {"执行状态": "已执行（自动化）",
              "实际结果": "未到保留期不告警；超期任务带逾期标记；删除任务列表仅管理员可访问。",
              "备注(依据)": "GET /admin/deletion-tasks 逾期口径",
              "自动化覆盖": "已覆盖",
              "自动化证据": f"{BND}::test_bv26_deletion_task_overdue_flag / "
                            "test_bv26_deletion_tasks_requires_admin"},

    # ==================== PERF 性能 ====================
    "PERF-01": {"执行状态": "已执行（自动化）",
                "实际结果": "真实服务实测 0.46s，首字节 ≤2s、整体 ≤10s 均达标。"
                            "本条原先每次回归都以「无历史报告」静默跳过——根因是用例请求了"
                            "不存在的 GET /reports 列表端点（report_router 只有 /{id}、/annotation、/pdf），"
                            "404 被当成无数据。已改用 GET /assessments 并在无历史记录时现造一份"
                            "（发起→作答→提交，与产品链路一致），指标现在真的在测。",
                "备注(依据)": "真实服务测量（SMOKE_BASE_URL）；GET /assessments/{id}/report/stream?ticket=",
                "自动化覆盖": "已覆盖",
                "自动化证据": f"{PRF}::test_perf01_report_latency（含 _completed_record 样本准备）"},
    "PERF-02": {"执行状态": "已执行（自动化）",
                "实际结果": "未达标，如实记录：真实服务 47 条不重复问题首字节 P95=2.137s（门槛 1.5s）、"
                            "完整回答 P95=12.474s（门槛 8s），3 条失败。分段实测：本地检索稳态 0.05s、"
                            "上游模型裸调首 token 0.717s、总 9.366s——耗时主要在第三方模型生成速度，"
                            "非本项目代码缺陷。缓存命中路径 20~40ms。",
                "备注(依据)": "模型 Qwen/Qwen2.5-72B-Instruct（第三方）；_perf02_probe.py / "
                             "_perf02_breakdown.py 实测",
                "自动化覆盖": "已覆盖",
                "自动化证据": f"{PRF}::test_perf02_chat_latency（实测未达标，保留失败结论）"},
    "PERF-03": {"执行状态": "已执行（自动化）",
                "实际结果": "图表查询 20 次 P95 ≤1.5s；智能解读 ≤5s，均达标。",
                "备注(依据)": "真实服务测量",
                "自动化覆盖": "已覆盖",
                "自动化证据": f"{PRF}::test_perf03_dashboard_latency"},
    "PERF-04": {"执行状态": "已执行（自动化）",
                "实际结果": "并发提交咨询全部接入不丢，用户侧有排队反馈。",
                "备注(依据)": "并发提交实测",
                "自动化覆盖": "已覆盖",
                "自动化证据": f"{PRF}::test_perf04_consultation_concurrency"},
    "PERF-05": {"执行状态": "已执行（自动化）",
                "实际结果": "本机无法造到 300 万行量级，如实按执行计划验证：报告表常用查询走索引，"
                            "不退化为全表扫。断言消息带实际行数与命中索引。",
                "备注(依据)": "EXPLAIN 执行计划（等效口径，如实说明差异）",
                "自动化覆盖": "部分覆盖",
                "自动化证据": f"{PRF}::test_perf05_large_table_query"},
    "PERF-06": {"执行状态": "已执行（自动化）",
                "实际结果": "本机无企业代理，如实断言 SSE 响应头已带禁止缓冲标记"
                            "（X-Accel-Buffering: no / no-cache），避免上线被 Nginx 缓冲。",
                "备注(依据)": "响应头断言（等效口径，如实说明差异）",
                "自动化覆盖": "部分覆盖",
                "自动化证据": f"{PRF}::test_perf06_proxy_buffering"},
    "PERF-07": {"执行状态": "已执行（自动化）",
                "实际结果": "并发保存草稿 P95 ≤200ms，达标。",
                "备注(依据)": "真实服务测量",
                "自动化覆盖": "已覆盖",
                "自动化证据": f"{PRF}::test_perf07_draft_save"},
    "PERF-08": {"执行状态": "已执行（自动化）",
                "实际结果": "按 1:5:20 比例混跑，无 5xx，P95 达标。",
                "备注(依据)": "混合负载实测",
                "自动化覆盖": "已覆盖",
                "自动化证据": f"{PRF}::test_perf08_mixed_load"},

    # ==================== UI 交互兼容 ====================
    "UI-01": {"执行状态": "已执行（自动化）",
              "实际结果": "静态断言：存在 <768px 响应式断点，报告区块在窄屏不溢出。"
                          "（像素级视觉回归属人工验收范围，未做。）",
              "备注(依据)": "HTML/CSS 静态断言",
              "自动化覆盖": "部分覆盖",
              "自动化证据": f"{PRF}::test_ui01_mobile_report_responsive"},
    "UI-02": {"执行状态": "已执行（自动化）",
              "实际结果": "静态断言通过：图表有等价数据表、语义标签齐全、"
                          "并补齐 prefers-reduced-motion 与 :focus-visible 无障碍样式（本轮修复项）。",
              "备注(依据)": "HTML/CSS 静态断言",
              "自动化覆盖": "已覆盖",
              "自动化证据": f"{PRF}::test_ui02_screen_reader_equivalents"},
}


def main():
    rows = load()
    before = sum(1 for r in rows if r["执行状态"] == "未执行")
    stat = fill(rows, MAPPING)
    dump(rows)
    after = sum(1 for r in rows if r["执行状态"] == "未执行")
    print(f"回填 {stat['filled']} 条；未执行 {before} → {after}（本次消化 {before - after} 条）")
    if stat["missing"]:
        print("未匹配编号：", stat["missing"])
    return 0 if not stat["missing"] else 1


if __name__ == "__main__":
    sys.exit(main())
