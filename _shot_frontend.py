# -*- coding: utf-8 -*-
"""本地渲染各屏截图，仅用于核对实际观感与新增页面是否渲染正常。不写入任何项目文件。

说明：用 file:// 打开前端，接口全部用 mock 顶替（只影响这个脚本里的浏览器上下文，
不影响真实页面）。要看真实数据请启动后端后用演示账号登录。
"""
import pathlib

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(r"D:/AI_health/frontend")
OUT = pathlib.Path(r"D:/AI_health/_shots")
OUT.mkdir(exist_ok=True)

MOCK = {
    "users": 80000, "new_users_today": 152,
    "assessments": 350000, "completed_assessments": 331200,
    "reports": 300000, "avg_score": 78.4,
    "chat_messages": 1600000, "knowledge_docs": 3000,
    "dau": 5120, "mau": 26000,
    "active_note": "登录 ∪ 提交测评 ∪ 发起咨询 ∪ 发起对话，去重",
    "month_assessment": 18400,
    "ai_ratio": 0.873, "ai_ratio_note": "营养师消息中 AI 辅助起草占比",
    "assessment_trend_7d": [
        {"date": "10-14", "count": 588}, {"date": "10-15", "count": 642},
        {"date": "10-16", "count": 705}, {"date": "10-17", "count": 668},
        {"date": "10-18", "count": 512}, {"date": "10-19", "count": 496},
        {"date": "10-20", "count": 631},
    ],
    "level_distribution": {"excellent": 8120, "good": 29640,
                           "needs_improvement": 12680, "poor": 3110, "unknown": 950},
    "template_usage": {
        "total_records": 350000, "template_count": 7, "used_template_count": 6,
        "published_template_count": 5,
        "items": [
            {"name": "膳食营养综合评估", "records": 168400, "status": "published"},
            {"name": "慢病风险筛查（成人）", "records": 96200, "status": "published"},
            {"name": "儿童生长发育评估", "records": 41300, "status": "published"},
            {"name": "孕期营养评估", "records": 24900, "status": "published"},
            {"name": "老年营养风险筛查", "records": 15800, "status": "draft"},
        ],
    },
    "generated_at": "2026-10-20 16:21:08",
    "note": "统计口径见《设计_01》§五",
}

AGENTS = [
    {"key": "orchestrator", "name": "智参主智能体", "role": "orchestrator",
     "desc": "调度中枢：识别需求意图、规划执行链路、分派六个子智能体并汇总结果",
     "enabled": True, "children": ["assessment", "diet", "dish", "steward", "dietitian", "data"]},
    {"key": "assessment", "name": "健康测评智能体", "role": "sub", "enabled": True,
     "desc": "舌象体质、营养问卷、慢病指标、OCR 体检报告解析与健康风险预警"},
    {"key": "diet", "name": "食养食疗智能体", "role": "sub", "enabled": True,
     "desc": "周期食养计划、药食同源知识融合、每日菜谱与采购清单"},
    {"key": "dish", "name": "智能荐菜智能体", "role": "sub", "enabled": True,
     "desc": "体质/慢病/过敏综合匹配，生成家庭、宴请、团队菜单"},
    {"key": "steward", "name": "健康管家智能体", "role": "sub", "enabled": True,
     "desc": "健康风险提醒、用餐与打卡提醒、健康饮品推荐"},
    {"key": "dietitian", "name": "营养师智能体", "role": "sub", "enabled": True,
     "desc": "用户营养问答、读取健康档案上下文、辅助人工营养师咨询"},
    {"key": "data", "name": "数据分析智能体", "role": "sub", "enabled": True,
     "desc": "Text2SQL、角色级行权限过滤、图表/AI 解读/智能报告"},
]

TOOLS_RUNTIME = {
    "agent": "assessment",
    "implemented_contract": ["plan", "tool", "checkpoint", "retry", "trace", "memory",
                             "approval_resume", "cost_budget", "compensation_hook"],
    "tools": [
        {"name": "assessment.execute", "description": "执行 assessment 业务 Agent 图",
         "mode": "read", "risk_level": "low", "requires_approval": False, "handler_registered": True},
        {"name": "health_profile.read", "description": "读取健康档案", "mode": "read",
         "risk_level": "low", "requires_approval": False, "handler_registered": True},
        {"name": "health_risk.calculate", "description": "计算健康风险", "mode": "read",
         "risk_level": "low", "requires_approval": False, "handler_registered": True},
        {"name": "health_report.publish", "description": "发布健康报告", "mode": "write",
         "risk_level": "high", "requires_approval": True, "handler_registered": True},
    ],
    "tool_count": 4,
    "approval_tools": ["health_report.publish"],
}

METRICS = {
    "generated_at": 1, "total_estimated_cost": 3.2,
    "agents": {"assessment": {"tasks": 1284, "completed": 1270, "failed": 14,
                              "waiting_approval": 2, "success_rate": 0.989, "p95_ms": 1840,
                              "estimated_cost": 1.2}},
    "tools": [{"agent": "assessment", "tool": "health_profile.read", "calls": 1240,
               "errors": 3, "error_rate": 0.0024, "p95_ms": 26}],
}

CHECKIN_STATS = {"week_start": "2026-10-19", "week_end": "2026-10-25", "week_checked": 3,
                 "week_total": 7, "week_rate": 43, "checked_today": True, "streak": 5,
                 "days": ["2026-10-19", "2026-10-20"]}

CHECKIN_LIST = {"total": 9, "days": 14, "items": [
    {"checkin_date": "2026-10-20", "check_type": "plan"},
    {"checkin_date": "2026-10-20", "check_type": "meal"},
    {"checkin_date": "2026-10-19", "check_type": "plan"},
    {"checkin_date": "2026-10-18", "check_type": "exercise"},
    {"checkin_date": "2026-10-17", "check_type": "plan"},
    {"checkin_date": "2026-10-15", "check_type": "weight"},
    {"checkin_date": "2026-10-14", "check_type": "plan"},
    {"checkin_date": "2026-10-13", "check_type": "meal"},
    {"checkin_date": "2026-10-11", "check_type": "plan"},
]}

NOTICES = {"total": 3, "unread": 2, "items": [
    {"id": 3, "type": "risk", "title": "空腹血糖偏高，建议复查",
     "content": "最近一次测评提示空腹血糖偏高，建议两周内复查并减少精制糖摄入。",
     "read": False, "created_at": "2026-10-20 08:12"},
    {"id": 2, "type": "checkin", "title": "今日还没有打卡",
     "content": "本日计划打卡尚未完成，晚饭后记得记录一次。", "read": False,
     "created_at": "2026-10-20 07:30"},
    {"id": 1, "type": "consultation", "title": "营养师已回复你的咨询",
     "content": "王营养师回复了你的咨询，可在营养师页面查看。", "read": True,
     "created_at": "2026-10-19 21:04"},
]}

HISTORY = {"total": 3, "items": [
    {"id": 3, "question": "统计我管理客户中糖尿病用户测评等级分布", "status": "success",
     "row_count": 5, "created_at": "2026-10-20 15:02"},
    {"id": 2, "question": "近30天高风险客户打卡完成率", "status": "success",
     "row_count": 1, "created_at": "2026-10-19 11:26"},
    {"id": 1, "question": "各测评模板使用次数", "status": "failed",
     "row_count": 0, "created_at": "2026-10-18 09:40"},
]}

MOCK_JS = """
(m) => {
  window.__MOCK__ = m;
  document.getElementById('loginPage')?.classList.add('hidden');
  try { CURRENT_USER = {role:'admin', username:'demo', real_name:'演示'}; } catch (e) {}
  try { if (typeof updateAuthUI === 'function') updateAuthUI(); } catch (e) {}

  API.admin.opsDashboard = async () => window.__MOCK__.ops;
  API.agents.list = async () => ({items: window.__MOCK__.agents, total: window.__MOCK__.agents.length,
                                  primary_key: 'orchestrator', source: 'default'});
  API.agents.detail = async (k) => window.__MOCK__.agents.find(a => a.key === k);
  API.agents.runtime = async () => window.__MOCK__.runtime;
  API.agents.metrics = async () => window.__MOCK__.metrics;
  API.checkins.stats = async () => window.__MOCK__.checkinStats;
  API.checkins.list = async () => window.__MOCK__.checkinList;
  API.checkins.create = async () => ({ok: true});
  API.notifications.list = async () => window.__MOCK__.notices;
  API.analytics.history = async () => window.__MOCK__.history;
  API.dashboard.trend = async () => ({points: [
    {date:'2026-04-12', template_name:'膳食营养综合评估', score:71, standardized_score:71, level:'良好'},
    {date:'2026-06-03', template_name:'膳食营养综合评估', score:74, standardized_score:74, level:'良好'},
    {date:'2026-07-21', template_name:'膳食营养综合评估', score:72, standardized_score:72, level:'良好'},
    {date:'2026-08-30', template_name:'膳食营养综合评估', score:79, standardized_score:79, level:'良好'},
    {date:'2026-10-11', template_name:'膳食营养综合评估', score:83, standardized_score:83, level:'优秀'}
  ], table:{columns:[],rows:[]}});
}
"""

PAYLOAD = {
    "ops": MOCK, "agents": AGENTS, "runtime": TOOLS_RUNTIME, "metrics": METRICS,
    "checkinStats": CHECKIN_STATS, "checkinList": CHECKIN_LIST, "notices": NOTICES,
    "history": HISTORY,
}


def run():
    url = (ROOT / "index.html").as_uri()
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1.5)
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(url, wait_until="load")
        pg.wait_for_timeout(400)
        pg.evaluate(MOCK_JS, PAYLOAD)

        shots = [
            ("01-home.png", "go('home')", 700, False),
            ("02-dash.png", "go('admin')", 2200, True),
            ("03-data.png", "go('data')", 1500, False),
            ("04-agents.png", "go('agents')", 900, False),
            ("05-agent-detail.png", "openAgentDetail('assessment')", 900, False),
            ("06-reminder.png", "go('reminder')", 1000, False),
            ("07-analytics.png", "go('analytics')", 900, False),
        ]
        for name, js, wait, full in shots:
            pg.evaluate("() => { %s; }" % js)
            pg.wait_for_timeout(wait)
            pg.screenshot(path=str(OUT / name), full_page=full)

        # 路由：hash 是否跟着切屏变
        hashes = pg.evaluate("() => [location.hash]")
        b.close()
    print("shots ->", OUT)
    print("hash after analytics:", hashes)
    if errs:
        print("PAGE ERRORS:")
        for e in errs:
            print("  -", e)
    else:
        print("no page errors")


if __name__ == "__main__":
    run()
