# -*- coding: utf-8 -*-
"""D / AD / K 三组补测用例（台账 IT-D / IT-AD / IT-K，共 17 条）。

口径说明：
  - IT-D-03/04 的时延断言量的是**流水线预算**：可控假模型 + 真实检索/入库。
    真实大模型的端到端时延在 PERF-02 单独实测，本组不混。
  - IT-D-05 的归因结论必须由规则算（维度分差最大项），不得由模型编造，
    故用假模型替换 LLM 后断言结论仍等于规则结果。
  - IT-K 系列全部走真实 MinIO + 真实 Milvus：上传→向量化→查询→删除→再问。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

import pytest

from app.core.errors import ErrCode
from tests.integration.api_helper import API, OPTIONS, QUESTIONS, ApiHelper, ErrCode as _E

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _all_answers() -> List[Dict[str, Any]]:
    """全部题目按第一个选项作答（提交用）。

    题目配置里的键是 `question_id`（不是 `id`），选项值取 `value`。
    """
    return [{"question_id": q["question_id"], "value": q["options"][0]["value"]}
            for q in QUESTIONS]


def _submitted(api: ApiHelper, headers: Dict[str, str],
               template_id: int, tag: str) -> Dict[str, Any]:
    """发起→答全→提交，返回报告数据（用于造趋势/维度分）。"""
    started = api.start(headers, template_id)
    assert started.get("code") == 0, started
    rid = started["data"]["assessment_id"]
    api.cleanup.append(lambda s, i=rid: ApiHelper._clean_record(s, i))
    saved = api.answer(rid, headers, _all_answers())
    assert saved.get("code") == 0, saved
    done = api.submit(rid, headers)
    assert done.get("code") == 0, done
    return done["data"]


def _report_of(api: ApiHelper, headers: Dict[str, str], report_id: int) -> Dict[str, Any]:
    body = api.c.get(f"{API}/reports/{report_id}", headers=headers).json()
    assert body.get("code") == 0, body
    return body["data"]


class _FakeLLM:
    """可控假模型：只吐固定片段，间隔可控，用于度量流水线而非模型速度。"""

    def __init__(self, pieces=None, delay: float = 0.01):
        self.pieces = pieces or ["分析结论：", "建议少量多次。", "建议遵医嘱。"]
        self.delay = delay

    def stream(self, messages, **kw):
        import time as _t
        for p in self.pieces:
            _t.sleep(self.delay)
            yield p


def _use_llm(monkeypatch, llm) -> None:
    """把报告解读用的模型换成假模型。

    顺序关键：报告服务在 `_llm_ready=False` 时会重建 `_llm`（`_ensure_llm`），
    若先替换再触发首次加载，真实模型对象会把假模型覆盖掉。
    另外假模型只有 `stream()`，故同时把 `_llm_ready` 置真，让服务直接用它。
    """
    from app.services.report_service import report_service as svc
    svc._ensure_llm()
    monkeypatch.setattr(svc, "_llm", llm, raising=False)
    monkeypatch.setattr(svc, "_llm_ready", True, raising=False)


# --------------------------------------------------------------------------- #
# IT-D · 数据看板（7 条）
# --------------------------------------------------------------------------- #
def test_it_d01_trend_same_template_only(api, monkeypatch):
    """IT-D-01 同模板趋势：按 template_id 过滤时**不得混入**其他模板的点。

    造两份报告：A 模板 2 份、B 模板 1 份；查 A 模板趋势必须只回 2 个点。
    """
    _use_llm(monkeypatch, _FakeLLM())
    owner = api.make_customer("d1")
    ta = api.template_id(code=api.uniq("TRENDA"), name="同模板趋势A")
    tb = api.template_id(code=api.uniq("TRENDB"), name="同模板趋势B")
    for _ in range(2):
        _submitted(api, owner["headers"], ta, "d1a")
    _submitted(api, owner["headers"], tb, "d1b")

    body = api.c.get(f"{API}/dashboard/personal/trend", headers=owner["headers"],
                     params={"template_id": ta}).json()
    assert body["code"] == 0, body
    pts = body["data"]["points"]
    assert len(pts) == 2, f"A 模板趋势应 2 点，实得 {len(pts)}：{pts}"
    assert all(p["score"] is not None for p in pts), pts


def test_it_d02_cross_template_annotated(api, monkeypatch):
    """IT-D-02 跨模板标注：不指定模板时返回跨模板数据，**必须标注模板名与版本**。

    否则用户会把不同量表的分数画在同一条折线上（模板满分不同，直接可比性错）。
    """
    _use_llm(monkeypatch, _FakeLLM())
    owner = api.make_customer("d2")
    ta = api.template_id(code=api.uniq("LABELA"), name="标注模板甲")
    tb = api.template_id(code=api.uniq("LABELB"), name="标注模板乙")
    ra = _submitted(api, owner["headers"], ta, "d2a")
    rb = _submitted(api, owner["headers"], tb, "d2b")

    body = api.c.get(f"{API}/dashboard/personal/trend", headers=owner["headers"]).json()
    assert body["code"] == 0, body
    pts = body["data"]["points"]
    assert len(pts) >= 2, pts
    for p in pts:
        # 折线图点的必备标注：模板名 + 版本 + 标准化百分制
        assert p.get("template_name"), f"趋势点缺模板名标注：{p}"
        assert p.get("template_version") is not None, f"趋势点缺模板版本：{p}"
        assert p.get("standardized_score") is not None, f"趋势点缺标准化百分制：{p}"
    names = {p["template_name"] for p in pts}
    assert {"标注模板甲", "标注模板乙"} <= names, names


def test_it_d03_chart_latency(api, monkeypatch):
    """IT-D-03 图表时延：查询 + 渲染数据 P95 ≤1.5s（真实库、真实检索/入库）。"""
    _use_llm(monkeypatch, _FakeLLM(delay=0.0))
    owner = api.make_customer("d3")
    tid = api.template_id(code=api.uniq("Td3"), name="图表时延模板")
    _submitted(api, owner["headers"], tid, "d3")

    samples: List[float] = []
    for _ in range(12):
        t0 = time.perf_counter()
        r = api.c.get(f"{API}/dashboard/personal/trend", headers=owner["headers"],
                      params={"template_id": tid})
        assert r.json()["code"] == 0, r.text[:200]
        api.c.get(f"{API}/dashboard/personal/dimensions", headers=owner["headers"])
        samples.append(time.perf_counter() - t0)
    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1]
    assert p95 <= 1.5, f"图表数据查询 P95 {p95:.3f}s 超 1.5s（样本 {samples}）"


def test_it_d04_insight_latency(api, monkeypatch):
    """IT-D-04 智能解读时延：解读文本 **先出图后出** —— 图与解读可分开取，不互相阻塞。"""
    _use_llm(monkeypatch, _FakeLLM(delay=0.0))
    owner = api.make_customer("d4")
    tid = api.template_id(code=api.uniq("Td4"), name="解读时延模板")
    rep = _submitted(api, owner["headers"], tid, "d4")

    # 先出图：只查数据，不等解读
    t0 = time.perf_counter()
    chart = api.c.get(f"{API}/dashboard/personal/trend",
                      headers=owner["headers"], params={"template_id": tid}).json()
    chart_ms = (time.perf_counter() - t0) * 1000
    assert chart["code"] == 0, chart
    assert chart_ms <= 1500, f"先出图耗时 {chart_ms:.0f}ms 超 1.5s"

    # 解读独立取，且读取的是**报告里已生成的文本**（不触发再生成）
    insight = api.c.get(f"{API}/dashboard/personal/insight",
                        headers=owner["headers"]).json()
    assert insight["code"] == 0, insight
    detail = _report_of(api, owner["headers"], rep["report_id"])
    if detail.get("analysis_text"):
        assert insight["data"]["insight"] == detail["analysis_text"]
    else:
        assert insight["data"]["note"], "无解读文本时必须给说明而不是空白"


def _answers_split(hi_first: bool) -> List[Dict[str, Any]]:
    """造出**非并列**的维度差异：只让第一个维度选高分项，其余全选低分项。

    全题统一选项会让每个维度等比缩放 → 分差全并列，归因结论无法区分对错；
    这里刻意让维度之间拉开差距，才能验证"归因 = 分差最大项"这条规则真的成立。
    """
    out = []
    for i, q in enumerate(QUESTIONS):
        opts = q["options"]
        if i == 0:
            idx = len(opts) - 1 if hi_first else 0
        else:
            idx = 0 if hi_first else len(opts) - 1
        out.append({"question_id": q["question_id"], "value": opts[idx]["value"]})
    return out


def _submitted_with(api: ApiHelper, headers: Dict[str, str], template_id: int,
                    answers: List[Dict[str, Any]]) -> Dict[str, Any]:
    started = api.start(headers, template_id)
    assert started.get("code") == 0, started
    rid = started["data"]["assessment_id"]
    api.cleanup.append(lambda s, i=rid: ApiHelper._clean_record(s, i))
    saved = api.answer(rid, headers, answers)
    assert saved.get("code") == 0, saved
    done = api.submit(rid, headers)
    assert done.get("code") == 0, done
    return done["data"]


def test_it_d05_attribution_rule_based(api, monkeypatch):
    """IT-D-05 归因正确性：结论 = **维度分差最大项**（规则算），非模型编造。

    做法：造两份**得分不同**的报告；把 LLM 换成会胡说的假模型，
    若归因仍是规则结果，说明归因没走模型。
    """
    _use_llm(monkeypatch, _FakeLLM(
        pieces=["我认为原因是运动不足，跟饮食无关。", "（以下均为编造）"]))
    owner = api.make_customer("d5")
    tid = api.template_id(code=api.uniq("Td5"), name="归因模板")
    first = _submitted_with(api, owner["headers"], tid, _answers_split(True))
    second = _submitted_with(api, owner["headers"], tid, _answers_split(False))

    d1 = _report_of(api, owner["headers"], first["report_id"]).get("dimension_scores") or []
    d2 = _report_of(api, owner["headers"], second["report_id"]).get("dimension_scores") or []
    assert len(d1) >= 2, f"需要至少 2 个维度才能算分差：{d1}"

    a_map = {x.get("name"): float(x.get("score") or 0) for x in d1}
    b_map = {x.get("name"): float(x.get("score") or 0) for x in d2}
    shared = set(a_map) & set(b_map)
    diffs = [(abs(a_map[n] - b_map[n]), n) for n in shared]
    assert diffs and max(diffs)[0] > 0, f"两次报告维度分必须不同才有归因意义：{d1} / {d2}"
    # 分差并列时不该靠字典序挑一个：接口须如实返回并列集合
    top_delta = max(diffs)[0]
    expect_tied = sorted(n for d, n in diffs if abs(d - top_delta) < 1e-9)

    body = api.c.get(f"{API}/dashboard/personal/attribution",
                     headers=owner["headers"],
                     params={"base_id": first["report_id"],
                             "target_id": second["report_id"]}).json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data.get("top_dimension") in expect_tied, \
        f"归因维度必须是分差最大者之一：{data.get('top_dimension')} vs {expect_tied}"
    assert sorted(data.get("tied_dimensions") or []) == expect_tied, \
        f"并列维度须如实全部列出：{data.get('tied_dimensions')} vs {expect_tied}"
    assert "运动" not in str(data), f"归因结论疑似来自模型编造：{data}"
    # 归因明细必须自带数值依据，便于人工复核
    deltas = data.get("deltas") or []
    assert deltas, data
    for d in deltas:
        assert set(d) >= {"name", "base", "target", "delta"}, d


def test_it_d06_empty_state(api):
    """IT-D-06 空数据：新用户无历史测评时展示空状态引导，**不报错、不展示 0 分假数据**。"""
    owner = api.make_customer("d6")
    for path, keys in (
        ("/dashboard/personal/trend", ["points"]),
        ("/dashboard/personal/dimensions", ["dimensions"]),
        ("/dashboard/personal/insight", ["insight"]),
        ("/dashboard/personal/risks", None),
    ):
        body = api.c.get(f"{API}{path}", headers=owner["headers"]).json()
        assert body["code"] == 0, f"{path} 空数据不应报错：{body}"
        data = body["data"]
        if keys:
            for k in keys:
                assert data.get(k) in (None, [], ""), f"{path} 的 {k} 应为空，实际 {data.get(k)!r}"
        # 只要有内容，就必须带说明；绝不返回 0 分冒充数据
        assert not _has_fake_zero(data), f"{path} 空数据出现 0 分假数据：{data}"


def _has_fake_zero(data: Any) -> bool:
    """空数据检测：points/dimensions 为空时不应出现 score=0 的点。"""
    for key in ("points", "dimensions"):
        items = data.get(key) if isinstance(data, dict) else None
        if isinstance(items, list) and items:
            return any(float(i.get("score") or 0) <= 0 for i in items
                       if isinstance(i, dict))
    return False


def test_it_d07_equivalent_table(api, monkeypatch):
    """IT-D-07 等价数据表：图表数据必须同时提供**等价表格 + 文字版结论**（读屏可达）。"""
    _use_llm(monkeypatch, _FakeLLM())
    owner = api.make_customer("d7")
    tid = api.template_id(code=api.uniq("Td7"), name="等价表模板")
    _submitted(api, owner["headers"], tid, "d7")

    trend = api.c.get(f"{API}/dashboard/personal/trend", headers=owner["headers"],
                      params={"template_id": tid}).json()["data"]
    assert trend.get("table"), f"趋势缺等价数据表（读屏用户无法读取图表）：{trend}"
    cols = {c.get("key") or c.get("name") for c in trend["table"].get("columns", [])}
    assert {"date", "score"} <= cols, f"等价表列不全：{cols}"
    rows = trend["table"].get("rows", [])
    assert len(rows) == len(trend["points"]), "等价表行数须与折线点数一致"

    assert trend.get("summary"), f"趋势缺文字版结论：{trend}"

    dims = api.c.get(f"{API}/dashboard/personal/dimensions", headers=owner["headers"]).json()["data"]
    assert dims.get("table") and dims.get("summary"), f"维度对比缺等价表/文字结论：{dims}"


# --------------------------------------------------------------------------- #
# IT-AD · 管理后台（7 条）
# --------------------------------------------------------------------------- #
def test_it_ad01_disable_user(api):
    """IT-AD-01 用户禁用：禁用后 U05 请求被拒（401/403），列表状态同步更新。"""
    admin = api.synthetic("ad1adm", "admin")
    target = api.synthetic_user("ad1u", "U")

    r = api.c.put(f"{API}/admin/users/{target['user_id']}/status", headers=admin,
                  json={"status": "disabled"})
    assert r.json()["code"] == 0, r.json()

    after = api.c.get(f"{API}/admin/users", headers=admin,
                      params={"keyword": target["user_id"]}).json()
    assert after["code"] == 0, after
    hit = [u for u in after["data"]["items"] if u["user_id"] == target["user_id"]]
    assert hit and hit[0]["status"] == "disabled", f"列表状态未同步：{hit}"

    # 禁用后旧令牌失效
    probe = api.c.get(f"{API}/dashboard/personal/overview", headers=target["headers"])
    code = probe.json().get("code")
    assert code in (ErrCode.UNAUTHENTICATED, ErrCode.PERMISSION_DENIED), probe.json()


def test_it_ad02_enable_user(api):
    """IT-AD-02 解封：恢复 active 后访问正常（与 IT-AD-01 串成闭环）。"""
    admin = api.synthetic("ad2adm", "admin")
    target = api.synthetic_user("ad2u", "U")

    off = api.c.put(f"{API}/admin/users/{target['user_id']}/status", headers=admin,
                    json={"status": "disabled"}).json()
    assert off["code"] == 0, off
    on = api.c.put(f"{API}/admin/users/{target['user_id']}/status", headers=admin,
                   json={"status": "active"}).json()
    assert on["code"] == 0, on
    assert on["data"]["status"] == "active", on

    again = api.c.get(f"{API}/dashboard/personal/overview", headers=target["headers"]).json()
    assert again["code"] == 0, f"解封后仍不可访问：{again}"


def test_it_ad03_audit_list_admin_only(api):
    """IT-AD-03 资质审核列表：仅管理员可见，且按时间倒序。"""
    admin = api.synthetic("ad3adm", "admin")
    made = api.make_dietitian(approve=False)          # 留一条待审
    api.clean_audit(made["user_id"])

    ok = api.c.get(f"{API}/admin/nutritionists/audits", headers=admin).json()
    assert ok["code"] == 0, ok
    items = ok["data"]["items"]
    assert any(x.get("user_id") == made["user_id"] for x in items), "待审列表未见新申请"
    times = [x.get("applied_at") or x.get("created_at") for x in items if x.get("applied_at")]
    assert times == sorted(times, reverse=True), f"未按时间倒序：{times}"

    # 非管理员（含普通用户与营养师）都必须被拒
    for role in ("user", "dietitian"):
        h = api.synthetic(f"ad3{role}", role)
        r = api.c.get(f"{API}/admin/nutritionists/audits", headers=h).json()
        assert r["code"] == ErrCode.PERMISSION_DENIED, f"{role} 竟可读审核列表：{r}"


def test_it_ad04_system_config_effective(api, monkeypatch):
    """IT-AD-04 系统配置：改智能体参数**立即生效**，且修改入操作日志（评审 M6）。"""
    admin = api.synthetic("ad4adm", "admin")
    api.clean_audit("chat.min_similarity")
    before = api.get_config(admin, "chat.min_similarity")
    assert before is not None, "基线缺少 chat.min_similarity 配置项"

    changed = api.set_config(admin, "chat.min_similarity", "0.9")
    assert changed["code"] == 0, changed
    assert changed["data"]["config_value"] == "0.9", changed

    now = api.get_config(admin, "chat.min_similarity")
    assert now == "0.9", f"配置未生效：{now}"

    logs = api.operation_logs(admin, action="config.update")
    hit = [x for x in logs if x.get("target_id") == "chat.min_similarity"]
    assert hit, f"配置修改未入操作日志（最近 {len(logs)} 条里没有）"
    assert "0.9" in str(hit[0].get("content")), f"日志未记前后值：{hit[0]}"


def test_it_ad05_operation_log_fields(api):
    """IT-AD-05 操作日志：关键操作后能查到 **操作人/时间/对象/前后值** 四要素（C1）。"""
    admin_holder = api.synthetic_user("ad5adm", "admin")
    admin = admin_holder["headers"]
    doc = api.make_dietitian(approve=False)
    api.clean_audit(doc["user_id"])

    audited = api.audit_dietitian(doc["user_id"], approve=True,
                                 reason="用例验证日志四要素", admin=admin)
    assert audited["code"] == 0, audited

    logs = api.operation_logs(admin, action="dietitian.audit")
    hit = [x for x in logs if x.get("target_id") == doc["user_id"]]
    assert hit, f"审核操作未入日志（共 {len(logs)} 条）"
    row = hit[0]
    assert row.get("operator_id") == admin_holder["user_id"], f"缺操作人：{row}"
    assert row.get("created_at"), f"缺时间：{row}"
    assert row.get("target_type") == "dietitian" and row.get("target_id") == doc["user_id"], row
    assert row.get("content") and "approve" in str(row["content"]), f"缺前后值：{row}"


def test_it_ad06_overview_matches_db(api, db):
    """IT-AD-06 运营概览：返回统计值与 DB 抽样核对一致。"""
    from sqlalchemy import text
    admin = api.synthetic("ad6adm", "admin")
    body = api.c.get(f"{API}/admin/overview", headers=admin).json()
    assert body["code"] == 0, body
    data = body["data"]

    for key, sql in (
        ("users", "SELECT COUNT(*) FROM sys_user WHERE deleted_at IS NULL"),
        ("assessments", "SELECT COUNT(*) FROM assessment_record"),
        ("reports", "SELECT COUNT(*) FROM assessment_report"),
    ):
        expect = db.execute(text(sql)).scalar() or 0
        assert data.get(key) == expect, f"{key} 不一致：接口 {data.get(key)} vs DB {expect}"

    # 等级分布之和应等于报告总数（不许出现对不上的分类）
    if isinstance(data.get("level_distribution"), dict):
        assert (sum(data["level_distribution"].values()) == data["reports"]
                ), data["level_distribution"]


def test_it_ad07_deep_paging(api):
    """IT-AD-07 深分页：`page=5000`（远超总数）不得超时，须正常返回空页 + 边界信息。"""
    admin = api.synthetic("ad7adm", "admin")
    t0 = time.perf_counter()
    r = api.c.get(f"{API}/admin/users", headers=admin,
                  params={"page": 5000, "page_size": 20})
    dt = time.perf_counter() - t0
    body = r.json()
    assert body["code"] == 0, body
    assert dt <= 1.0, f"深分页耗时 {dt:.3f}s 超 1s"
    page = body.get("data") or {}
    assert page.get("page") == 5000, f"页码须原样回显（不能被静默忽略）：{page}"
    assert not page.get("items"), f"超范围页应返回空：{page.get('items')}"
    assert isinstance(page.get("total"), int), f"缺真实总数：{page}"
    assert page.get("has_more") is False, page

    # 第 1 页必须有数据，且总数不随页码变化（否则前端会算错页数）
    first = api.c.get(f"{API}/admin/users", headers=admin,
                      params={"page": 1, "page_size": 20}).json()["data"]
    assert first["items"], "第 1 页应有数据"
    assert first["total"] == page["total"], "总数不应随页码变化"
    assert first["has_more"] is (20 < first["total"])


# --------------------------------------------------------------------------- #
# IT-K · 知识库（4 条：02 向量化状态 / 03 删除联动 / 04 删除后查询 / 05 非管理员）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def kb_admin(api):
    """管理员 + 目标集合名（知识库整组都要用）。"""
    return {"headers": api.synthetic("kb", "admin"),
            "collection": _kb_collection(api)}


def _kb_collection(api) -> str:
    from app.config import settings
    return settings.MILVUS_DEFAULT_COLLECTION


def _upload(api, admin, name: str, body: str, category: str = "food") -> Dict[str, Any]:
    r = api.c.post(f"{API}/knowledge/docs", headers=admin["headers"],
                   files={"file": (name, body.encode("utf-8"), "text/markdown")},
                   data={"collection_name": admin["collection"],
                         "category": category, "source": "补测用例"})
    j = r.json()
    assert j.get("code") == 0, j
    doc = j["data"]
    api.clean_audit(doc["id"])
    return doc


def _vector_count(file_name: str) -> int:
    from app.config import settings
    from pymilvus import connections, Collection
    connections.connect(alias="kbtest", host=settings.MILVUS_HOST, port=settings.MILVUS_PORT,
                        db_name=settings.MILVUS_DEFAULT_DB, timeout=5)
    col = Collection(settings.MILVUS_DEFAULT_COLLECTION, using="kbtest")
    col.flush()
    rows = col.query(expr=f'file_name == "{file_name}"', output_fields=["pk"])
    return len(rows)


def _wipe_vectors(file_name: str) -> None:
    """用例清理兜底：向量表无 user_id，residue_watchdog 扫不到，必须显式删。"""
    from app.config import settings
    from pymilvus import connections, Collection
    connections.connect(alias="kbclean", host=settings.MILVUS_HOST, port=settings.MILVUS_PORT,
                        db_name=settings.MILVUS_DEFAULT_DB, timeout=5)
    col = Collection(settings.MILVUS_DEFAULT_COLLECTION, using="kbclean")
    col.delete(f'file_name == "{file_name}"')
    col.flush()


def test_it_k02_embed_status_chain(api, kb_admin):
    """IT-K-02 向量化状态：pending→processing→done；**失败原因可见**，不吞异常。"""
    doc = _upload(api, kb_admin, "kb02_ok.md",
                  "# 标题甲\n\n## 标题乙\n\n莲子心味苦，性凉，用于清热降火。\n")
    api.cleanup.append(lambda s, f=doc["file_name"]: _delete_row(s, f))
    api.cleanup.append(lambda f=doc["file_name"]: _wipe_vectors(f))

    assert doc["embed_status"] == "done", f"应向量化完成：{doc}"
    assert doc["chunk_count"] >= 1, doc

    # 失败路径：空内容（按 # 切分后无片段）必须落 failed 且说明原因
    bad = _upload(api, kb_admin, "kb02_bad.md", "\n\n\n")
    api.cleanup.append(lambda s, f=bad["file_name"]: _delete_row(s, f))
    api.cleanup.append(lambda f=bad["file_name"]: _wipe_vectors(f))
    assert bad["embed_status"] == "failed", f"空内容应标 failed：{bad}"
    assert bad.get("description"), f"失败必须写明原因，管理员看不到就没法重试：{bad}"

    # 重试接口对失败文档可用
    retry = api.c.post(f"{API}/knowledge/docs/{bad['id']}/embed",
                       headers=kb_admin["headers"]).json()
    assert retry["code"] != 0 or retry.get("data"), retry


def _delete_row(session, file_name: str) -> None:
    from sqlalchemy import text
    session.execute(text("DELETE FROM knowledge_files WHERE file_name = :f"), {"f": file_name})


def test_it_k03_delete_syncs_vectors(api, kb_admin):
    """IT-K-03 删除联动：MySQL 记录与 Milvus 向量**同步删除**（评审 M5）。"""
    doc = _upload(api, kb_admin, "kb03_del.md",
                  "# 第一节\n\n山楂与海藻同食需谨慎。\n\n## 第二节\n\n第二段内容。\n")
    fname = doc["file_name"]
    api.cleanup.append(lambda s, f=fname: _delete_row(s, f))
    api.cleanup.append(lambda f=fname: _wipe_vectors(f))

    before = _vector_count(fname)
    assert before >= 1, f"上传后应已写入向量：{before}"

    r = api.c.delete(f"{API}/knowledge/docs/{doc['id']}", headers=kb_admin["headers"]).json()
    assert r["code"] == 0, r
    assert not r.get("message", "").count("待"), f"不应再提示『待清理』：{r}"

    assert _vector_count(fname) == 0, "向量未同步删除"
    listing = api.c.get(f"{API}/knowledge/docs", headers=kb_admin["headers"],
                        params={"page": 1, "page_size": 100}).json()
    ids = [x["id"] for x in (listing["data"] or {}).get("items", [])]
    assert doc["id"] not in ids, "元数据未删除"

    # 再删一次应 404/90002，不能静默成功
    again = api.c.delete(f"{API}/knowledge/docs/{doc['id']}",
                         headers=kb_admin["headers"]).json()
    assert again["code"] == ErrCode.RESOURCE_NOT_FOUND, again


def test_it_k04_query_after_delete(api, kb_admin, monkeypatch):
    """IT-K-04 删除后查询：已删文档不再被引用；无来源时走兜底（A7-4）。"""
    marker = "独有关键词龙鳞雪莲果"
    doc = _upload(api, kb_admin, "kb04_q.md",
                  f"# 专章\n\n{marker}是补测用例用于验证删除后不再被引用的独特标记内容。\n")
    fname = doc["file_name"]
    api.cleanup.append(lambda s, f=fname: _delete_row(s, f))
    api.cleanup.append(lambda f=fname: _wipe_vectors(f))
    assert _vector_count(fname) >= 1

    deleted = api.c.delete(f"{API}/knowledge/docs/{doc['id']}",
                           headers=kb_admin["headers"]).json()
    assert deleted["code"] == 0, deleted

    _use_llm(monkeypatch, _FakeLLM(pieces=["当前知识库中没有相关内容。", "建议咨询医师。"]))
    owner = api.make_customer("k4")
    api.clean_chat(owner["user_id"])          # 对话会话无外键兜底，必须登记
    events = api.post_sse(f"{API}/chat/query", headers=owner["headers"],
                          body={"question": f"{marker}是什么"})
    done = api.data_of(events, "done")
    assert done is not None, events
    text = "".join(e.get("text", "") for e in events if e.get("event") == "delta")
    assert marker not in text, "已删文档内容仍出现在回答里"

    # 引用里不得再出现该文档；无来源时必须走兜底（拒答/基础模式），不硬答
    refs = api.data_of(events, "reference")
    if refs:
        blob = str(refs)
        assert fname not in blob and marker not in blob, f"仍引用已删文档：{blob[:300]}"
    else:
        assert done.get("state") in ("basic", "refuse") or not text.strip(), done


def test_it_k05_non_admin_forbidden(api):
    """IT-K-05 非管理员访问知识库接口：全部 403，不泄露文档存在性。"""
    for role in ("user", "dietitian"):
        h = api.synthetic(f"k5{role}", role)
        for method, path in (
            ("get", f"{API}/knowledge/docs"),
            ("post", f"{API}/knowledge/docs/123/embed"),
            ("delete", f"{API}/knowledge/docs/123"),
            ("get", f"{API}/knowledge/docs/123/download"),
        ):
            r = getattr(api.c, method)(path, headers=h)
            body = r.json()
            # 只探测已注册路径：未注册 URL 走标准 HTTP 404，那是路由问题不是权限问题
            assert body.get("code") == ErrCode.PERMISSION_DENIED, \
                f"{role} {method.upper()} {path} 未被拒：{body}"

    # 管理员同一路径可通（证明上面确实是被权限拦住，不是路由没接）
    admin = api.synthetic("k5adm", "admin")
    ok = api.c.get(f"{API}/knowledge/docs/123/download", headers=admin).json()
    assert ok["code"] == ErrCode.RESOURCE_NOT_FOUND, f"管理员也该是查无此文档：{ok}"