# -*- coding: utf-8 -*-
"""集成用例辅助层：把 153 条补测用例反复要用到的前置动作收敛到一处。

覆盖：注册 / 登录 / 令牌 / 短信验证码 / 建模板（含发布态）/ 发起测评 /
草稿 / 提交 / 报告 / 短票 / 营养师资质 / 绑定 / 对话 / 清理登记。

约定（与 tests/integration/conftest.py 一致）：
  - 所有造数带 `run_id` 后缀，互不干扰；
  - 每个造数动作**立刻**登记清理函数，避免用例中途失败留下残留；
  - 断言以 `app/core/errors.py` 的**已落地错误码**为准，不以旧版用例集编号为准。
"""
from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional

from app.core.errors import ErrCode
from tests.integration.conftest import DIMS, OPTIONS, QUESTIONS

PASSWORD = "Passw0rd!2026"
API = "/api/v1"

# 进程内全局序号：用例之间也要唯一。
# 若按「每个用例各从 1 开始」，不同用例会生成同名账号（注册 20002 冲突）
# 与同一手机号（短信 60s 限频 60003 直接失败），互相污染结果。
_GLOBAL_SEQ = itertools.count(1)


class ApiHelper:
    """面向用例的 API 门面。"""

    PASSWORD = PASSWORD          # 便于用例内以 api.PASSWORD 引用，保持口径一致

    def __init__(self, client, run_id: str, cleanup: list, db):
        self.c = client
        self.run_id = run_id
        self.cleanup = cleanup
        self.db = db
        self._seq = 0

    # ------------------------------------------------------------------ #
    # 命名
    # ------------------------------------------------------------------ #
    def uniq(self, prefix: str) -> str:
        """生成带 run_id 的唯一标识（进程内自增，跨用例不重复）。"""
        return f"{prefix}_{self.run_id}_{next(_GLOBAL_SEQ)}"

    def phone(self) -> str:
        """生成合法且互不相同的手机号：138 + run_id 尾 5 位 + 3 位全局序号 = 11 位。

        短信验证码按手机号 60s 限频，号码重复会直接触发 60003，
        因此必须保证全局唯一（3 位序号支持同轮 1000 个不同号码）。
        """
        digits = ("".join(ch for ch in self.run_id if ch.isdigit())[-5:]).rjust(5, "0")
        return f"138{digits}{next(_GLOBAL_SEQ) % 1000:03d}"

    def email(self) -> str:
        """生成合法且互不相同的邮箱。

        域名用 RFC 2606 保留的 `.invalid`：它**永不解析、永不投递**，
        即便某次误配了真实 SMTP 也不会把验证码邮件发给陌生人。
        统一小写——`check_email` 会归一化，用例内直接用小写串比较好断言。
        """
        return f"{self.uniq('m')}@example.invalid".lower()

    # ------------------------------------------------------------------ #
    # 账号
    # ------------------------------------------------------------------ #
    def register(self, username: Optional[str] = None, phone: Optional[str] = None,
                 password: str = PASSWORD, real_name: str = "",
                 verify_code: Optional[str] = None) -> Dict[str, Any]:
        """注册（账号通道或手机号通道）。手机号通道未给验证码时自动取演示码。"""
        if phone and verify_code is None:
            verify_code = self.sms_code(phone)
        body: Dict[str, Any] = {"password": password, "real_name": real_name}
        if phone:
            body.update(phone=phone, verify_code=verify_code)
        else:
            body.update(username=username or self.uniq("u"))
        r = self.c.post(f"{API}/auth/register", json=body).json()
        if r.get("code") == 0:
            uid = r["data"]["user_id"]
            uname = r["data"]["username"]
            self.cleanup.append(lambda s, u=uname: self._clean_user(s, u))
            self.cleanup.append(lambda s, i=uid: self._clean_profile(s, i))
        return r

    def login(self, username: str, password: str = PASSWORD) -> Dict[str, Any]:
        return self.c.post(f"{API}/auth/login",
                           json={"username": username, "password": password}).json()

    def login_phone(self, phone: str, password: str = PASSWORD) -> Dict[str, Any]:
        return self.c.post(f"{API}/auth/login",
                           json={"phone": phone, "password": password}).json()

    def login_registered(self, username: Optional[str] = None,
                         phone: Optional[str] = None) -> Dict[str, Any]:
        """注册 + 登录一步到位，返回登录响应体。"""
        reg = self.register(username=username, phone=phone)
        assert reg["code"] == 0, reg
        if phone:
            body = self.login_phone(phone)
            uname = reg["data"]["username"]
        else:
            uname = username or reg["data"]["username"]
            body = self.login(uname)
        assert body["code"] == 0, body
        return body

    def hdr(self, token_body: Dict[str, Any]) -> Dict[str, str]:
        return {"Authorization": f"Bearer {token_body['data']['access_token']}"}

    def synthetic(self, tag: str, role: str = "user") -> Dict[str, str]:
        """合成令牌：用于只关心鉴权与业务分支的用例。

        2026-10-16 起鉴权会校验账号状态（禁用后旧令牌立即失效），
        故这里同步建好 sys_user 行（幂等），并登记清理。
        """
        from app.utils.security import create_access_token
        from tests.integration.conftest import _ensure_sys_user

        tag = self.uniq(tag)
        user_id = f"{self.run_id}_{tag}"
        _ensure_sys_user(user_id, role)
        self.cleanup.append(lambda s, i=user_id: self._clean_synthetic(s, i))
        return {"Authorization": f"Bearer {create_access_token(user_id, tag, role)}"}

    def synthetic_user(self, tag: str = "U", role: str = "user") -> Dict[str, Any]:
        """返回 {user_id, headers} 的合成用户（需要 user_id 时用这个）。"""
        tag = self.uniq(tag)
        user_id = f"{self.run_id}_{tag}"
        from app.utils.security import create_access_token
        from tests.integration.conftest import _ensure_sys_user

        _ensure_sys_user(user_id, role)
        self.cleanup.append(lambda s, i=user_id: self._clean_synthetic(s, i))
        return {"user_id": user_id,
                "headers": {"Authorization": f"Bearer {create_access_token(user_id, tag, role)}"}}

    def user_id_of(self, name: str) -> str:
        from app.models.mysql.user import SysUser
        u = self.db.query(SysUser).filter_by(username=name).first()
        return u.user_id if u else ""

    def guest(self) -> Dict[str, Any]:
        """游客身份：{token, headers}。游客不落 sys_user，无需清理。"""
        from app.utils.security import create_guest_token
        token = create_guest_token()
        return {"token": token, "headers": {"Authorization": f"Bearer {token}"}}

    def claim(self, headers: Dict[str, str], guest_token: str,
              phone: Optional[str] = None, verify_code: Optional[str] = None) -> Dict[str, Any]:
        """游客数据认领（接口层必须带 phone + verify_code，评审 M2）。"""
        body: Dict[str, Any] = {"guest_token": guest_token}
        if phone is not None:
            body["phone"] = phone
        if verify_code is not None:
            body["verify_code"] = verify_code
        return self.c.post(f"{API}/assessments/guest/claim", headers=headers, json=body).json()

    # ------------------------------------------------------------------ #
    # 短信验证码
    # ------------------------------------------------------------------ #
    def sms_code(self, phone: str) -> str:
        """发送验证码并返回演示码（开发态 SMS_DEMO_MODE=true 时响应带 demo_code）。"""
        r = self.c.post(f"{API}/auth/sms/send", json={"phone": phone}).json()
        assert r.get("code") == 0, r
        code = (r.get("data") or {}).get("demo_code")
        assert code, f"演示模式应回传 demo_code：{r}"
        return code

    def peek_sms_code(self, phone: str) -> str:
        """取 Redis 里**当前有效**的验证码，不触发新的发送。

        用在同一用例里"先试错、再用对的"这类场景：短信按号 60s 限频，
        第二次 send 必然 60003，此时该复用同一个码而不是重新申请。
        """
        from app.utils.redis_client import redis_client
        from app.utils.security import phone_hmac
        raw = redis_client.get(f"sms:code:{phone_hmac(phone)}")
        assert raw, f"Redis 中没有 {phone} 的有效验证码（是否已过期？）"
        return str(raw)

    # ------------------------------------------------------------------ #
    # 邮箱验证码（FR-U-01）
    # ------------------------------------------------------------------ #
    def email_code(self, email: str) -> str:
        """发送邮箱验证码并返回演示码（开发态 MAIL_DEMO_MODE=true 时响应带 demo_code）。"""
        r = self.c.post(f"{API}/auth/email/send", json={"email": email}).json()
        assert r.get("code") == 0, r
        code = (r.get("data") or {}).get("demo_code")
        assert code, f"演示模式应回传 demo_code：{r}"
        return code

    def peek_email_code(self, email: str) -> str:
        """取 Redis 里当前有效的邮箱验证码（不触发新发送，避开 60s 限频）。"""
        from app.utils.redis_client import redis_client
        from app.utils.security import email_hash
        raw = redis_client.get(f"email:code:{email_hash(email)}")
        assert raw, f"Redis 中没有 {email} 的有效验证码（是否已过期？）"
        return str(raw)

    def register_by_email(self, email: Optional[str] = None, code: Optional[str] = None,
                          password: str = PASSWORD, real_name: str = "") -> Dict[str, Any]:
        """邮箱通道注册；未给验证码时自动取演示码。"""
        email = email or self.email()
        if code is None:
            code = self.email_code(email)
        r = self.c.post(f"{API}/auth/register", json={
            "email": email, "email_code": code, "password": password, "real_name": real_name,
        }).json()
        if r.get("code") == 0:
            uname = r["data"]["username"]
            uid = r["data"]["user_id"]
            self.cleanup.append(lambda s, u=uname: self._clean_user(s, u))
            self.cleanup.append(lambda s, i=uid: self._clean_profile(s, i))
        return r

    def login_email(self, email: str, password: str = PASSWORD) -> Dict[str, Any]:
        return self.c.post(f"{API}/auth/login",
                           json={"email": email, "password": password}).json()

    @staticmethod
    def clear_email_code(email: str) -> None:
        """清掉某邮箱的验证码/限频键（用例结束兜底，避免 60s 限频影响后续）。"""
        from app.utils.redis_client import redis_client
        from app.utils.security import email_hash
        h = email_hash(email)
        redis_client.delete(f"email:code:{h}", f"email:try:{h}", f"email:limit:{h}")

    # ------------------------------------------------------------------ #
    # 模板
    # ------------------------------------------------------------------ #
    def make_template(self, code: Optional[str] = None, name: Optional[str] = None,
                      dims: Optional[List[Dict]] = None, questions: Optional[List[Dict]] = None,
                      publish: bool = True, admin: Optional[Dict[str, str]] = None,
                      creator: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """建模板；publish=True 时走「提交审核 → 审核通过」，成为用户可见模板。

        返回创建响应体（data 含 id / status / version）。
        """
        code = code or self.uniq("TPL")
        headers = creator or admin or self.synthetic("tpladm", "admin")
        body = {
            "code": code, "name": name or f"补测模板 {self.run_id}",
            "type": "nutrition",
            "dimension_config": dims if dims is not None else DIMS,
            "question_config": questions if questions is not None else QUESTIONS,
        }
        r = self.c.post(f"{API}/templates", headers=headers, json=body).json()
        assert r.get("code") == 0, r
        tpl_id = r["data"]["id"]
        self.cleanup.append(lambda s, c=code: self._clean_template(s, c))
        if publish:
            a = self.c.post(f"{API}/templates/{tpl_id}/submit-audit", headers=headers).json()
            assert a.get("code") == 0, a
            audit_hdr = admin or self.synthetic("tplaud", "admin")
            b = self.c.post(f"{API}/templates/{tpl_id}/audit",
                            headers=audit_hdr, json={"approve": True}).json()
            assert b.get("code") == 0, b
        return r

    def template_id(self, **kw) -> int:
        return self.make_template(**kw)["data"]["id"]

    # ------------------------------------------------------------------ #
    # 测评
    # ------------------------------------------------------------------ #
    def start(self, headers: Dict[str, str], template_id: int, **extra) -> Dict[str, Any]:
        body = {"template_id": template_id}
        body.update(extra)
        r = self.c.post(f"{API}/assessments", headers=headers, json=body).json()
        if r.get("code") == 0:
            rid = r["data"]["assessment_id"]
            self.cleanup.append(lambda s, i=rid: self._clean_record(s, i))
        return r

    def record_id(self, headers: Dict[str, str], template_id: int, **extra) -> int:
        return self.start(headers, template_id, **extra)["data"]["assessment_id"]

    def answer(self, record_id: int, headers: Dict[str, str],
               answers: Optional[Dict[str, Any]] = None, page: int = 1,
               version: Any = None) -> Dict[str, Any]:
        """保存草稿。version 传上次返回的 saved_at 时启用多端并发保护（IT-AS-18）。"""
        if answers is None:
            answers = {q["question_id"]: "A" for q in QUESTIONS}
        body: Dict[str, Any] = {"answers": answers, "current_page": page}
        if version is not None:
            body["version"] = version
        return self.c.patch(f"{API}/assessments/{record_id}/answers", headers=headers,
                            json=body).json()

    def submit(self, record_id: int, headers: Dict[str, str],
               key: Optional[str] = None) -> Dict[str, Any]:
        """提交测评。key 缺省时自动生成——接口层 Idempotency-Key 必填（IT-AS-11）。

        需要验证「不带键被拒」的用例，请直接用 `api.c.post` 发原始请求。
        """
        h = dict(headers)
        h["Idempotency-Key"] = key if key is not None else self.uniq("idem")
        return self.c.post(f"{API}/assessments/{record_id}/submit", headers=h).json()

    def ticket(self, headers: Dict[str, str]) -> str:
        r = self.c.post(f"{API}/auth/stream-ticket", headers=headers).json()
        assert r.get("code") == 0, r
        return r["data"]["ticket"]

    def sse_events(self, url: str, **kw) -> List[Dict[str, Any]]:
        """读取一次 SSE 响应，解析为 [{event, data}] 列表。

        TestClient 对 stream 响应会一次性取回全文，按 SSE 帧（空行分隔）切分即可。
        """
        import json as _json

        r = self.c.get(url, **kw)
        if r.headers.get("content-type", "").startswith("application/json"):
            return [{"event": "_json", "data": r.json()}]
        events: List[Dict[str, Any]] = []
        cur_event, cur_data = None, []
        for line in r.text.splitlines():
            if line.startswith("event:"):
                cur_event = line[6:].strip()
            elif line.startswith("data:"):
                cur_data.append(line[5:].strip())
            elif line == "":
                if cur_event is not None:
                    raw = "\n".join(cur_data)
                    try:
                        payload = _json.loads(raw) if raw else None
                    except Exception:                     # noqa: BLE001
                        payload = raw
                    events.append({"event": cur_event, "data": payload})
                cur_event, cur_data = None, []
        return events

    def post_sse(self, path: str, headers: Dict[str, str], body: Dict[str, Any]) -> List[Dict[str, Any]]:
        """POST 版 SSE：/chat/query 是 POST 流，TestClient 直接取 text 解析。"""
        import json as _json

        r = self.c.post(path, headers=headers, json=body)
        if r.headers.get("content-type", "").startswith("application/json"):
            return [{"event": "_json", "data": r.json()}]
        events: List[Dict[str, Any]] = []
        cur_event, cur_data = None, []
        for line in r.text.splitlines():
            if line.startswith("event:"):
                cur_event = line[6:].strip()
            elif line.startswith("data:"):
                cur_data.append(line[5:].strip())
            elif line == "":
                if cur_event is not None:
                    raw = "\n".join(cur_data)
                    try:
                        payload = _json.loads(raw) if raw else None
                    except Exception:                     # noqa: BLE001
                        payload = raw
                    events.append({"event": cur_event, "data": payload})
                cur_event, cur_data = None, []
        return events

    @staticmethod
    def event(events: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
        """取指定事件的**完整包装**（含 event/data 两个键）。

        要数据体请用 `data_of(events, name)`：直接对返回的包装取字段会拿到 None，
        那种断言看着在验、其实什么都没验。
        """
        for e in events:
            if e["event"] == name:
                return e
        return None

    @staticmethod
    def data_of(events: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
        """取指定事件的 data 体（不存在返回 None）。"""
        for e in events:
            if e["event"] == name:
                return e.get("data")
        return None

    def get_sse(self, path: str, headers: Dict[str, str],
                params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """GET 版 SSE：报告流这类通道走 `?ticket=` 短票，不是 POST + body。"""
        r = self.c.get(path, headers=headers, params=params or {})
        return self._parse_sse(r)

    def _parse_sse(self, r) -> List[Dict[str, Any]]:
        import json as _json
        if r.headers.get("content-type", "").startswith("application/json"):
            return [{"event": "_json", "data": r.json()}]
        events: List[Dict[str, Any]] = []
        cur_event, cur_data = None, []
        for line in r.text.splitlines():
            if line.startswith("event:"):
                cur_event = line[6:].strip()
            elif line.startswith("data:"):
                cur_data.append(line[5:].strip())
            elif line == "":
                if cur_event is not None:
                    raw = "\n".join(cur_data)
                    try:
                        payload = _json.loads(raw) if raw else None
                    except Exception:                     # noqa: BLE001
                        payload = raw
                    events.append({"event": cur_event, "data": payload})
                cur_event, cur_data = None, []
        return events

    def report_stream(self, record_id: int, headers: Dict[str, str]) -> List[Dict[str, Any]]:
        """走报告流式通道（自带短票）。EX 组大量用例用它注入故障。"""
        ticket = self.ticket(headers)
        return self.get_sse(f"{API}/assessments/{record_id}/report/stream",
                            headers={}, params={"ticket": ticket})

    # ------------------------------------------------------------------ #
    # 营养师
    # ------------------------------------------------------------------ #
    def dietitian_apply(self, headers: Dict[str, str], cert_no: Optional[str] = None) -> Dict[str, Any]:
        return self.c.post(f"{API}/nutritionists/apply", headers=headers, json={
            "cert_name": "注册营养师",
            "cert_no": cert_no or self.uniq("CERT"),
            "specialties": ["慢病饮食"],
            "cases": "补测用例造数",
        }).json()

    def audit_dietitian(self, target_user_id: str, approve: bool = True,
                        reason: str = "", admin: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        hdr = admin or self.synthetic("auditadm", "admin")
        # 入参字段名以实现为准：AuditReq = {approve, reason}（reject_reason 是 DB 列名，不是接口字段）
        return self.c.post(f"{API}/admin/nutritionists/{target_user_id}/audit", headers=hdr,
                           json={"approve": approve, "reason": reason}).json()

    def make_dietitian(self, approve: bool = True) -> Dict[str, Any]:
        """造一名营养师：合成账号（role=dietitian）+ 提交资质 +（可选）审核通过。

        返回 {user_id, headers}。资质写 dietitian_profile、绑定写 dietitian_customer、
        账号写 sys_user，三处都要登记清理。
        """
        from app.utils.security import create_access_token
        from tests.integration.conftest import _ensure_sys_user

        tag = self.uniq("N")
        user_id = f"{self.run_id}_{tag}"
        _ensure_sys_user(user_id, "dietitian")
        self.cleanup.append(lambda s, i=user_id: self._clean_synthetic(s, i))
        headers = {"Authorization": f"Bearer {create_access_token(user_id, tag, 'dietitian')}"}
        applied = self.dietitian_apply(headers)
        assert applied.get("code") == 0, applied
        if approve:
            audited = self.audit_dietitian(user_id)
            assert audited.get("code") == 0, audited
        return {"user_id": user_id, "headers": headers, "apply": applied}

    def make_customer(self, tag: str = "C") -> Dict[str, Any]:
        """造一名普通用户（合成账号），返回 {user_id, headers}。"""
        from app.utils.security import create_access_token
        from tests.integration.conftest import _ensure_sys_user

        tag = self.uniq(tag)
        user_id = f"{self.run_id}_{tag}"
        _ensure_sys_user(user_id, "user")
        self.cleanup.append(lambda s, i=user_id: self._clean_synthetic(s, i))
        return {"user_id": user_id,
                "headers": {"Authorization": f"Bearer {create_access_token(user_id, tag, 'user')}"}}

    def invite_code(self, dietitian_headers: Dict[str, str]) -> str:
        r = self.c.post(f"{API}/nutritionists/me/invite-code", headers=dietitian_headers).json()
        assert r.get("code") == 0, r
        return r["data"]["invite_code"]

    def bind_by_code(self, customer_headers: Dict[str, str], code: str) -> Dict[str, Any]:
        return self.c.post(f"{API}/nutritionists/me/customers/bind", headers=customer_headers,
                           json={"invite_code": code}).json()

    # ------------------------------------------------------------------ #
    # 清理（各自提交，单项失败不牵连）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _clean_user(session, username: str) -> None:
        from app.models.mysql.platform import UserProfile
        from app.models.mysql.user import SysUser
        u = session.query(SysUser).filter_by(username=username).first()
        if u:
            session.query(UserProfile).filter_by(user_id=u.user_id).delete()
            session.query(SysUser).filter_by(user_id=u.user_id).delete()

    @staticmethod
    def _clean_user_ids(session, *user_ids) -> None:
        from app.models.mysql.platform import UserProfile
        ids = [i for i in user_ids if i]
        if ids:
            session.query(UserProfile).filter(
                UserProfile.user_id.in_(ids)).delete(synchronize_session=False)

    @staticmethod
    def _clean_profile(session, user_id: str) -> None:
        from app.models.mysql.platform import UserProfile
        if user_id:
            session.query(UserProfile).filter_by(user_id=user_id).delete()

    @staticmethod
    def _clean_dietitian(session, user_id: str) -> None:
        """营养师侧清理：资质档案 + 其名下的绑定关系。"""
        from app.models.mysql.platform import DietitianCustomer, DietitianProfile
        if not user_id:
            return
        session.query(DietitianProfile).filter_by(user_id=user_id).delete()
        session.query(DietitianCustomer).filter_by(dietitian_id=user_id).delete()

    @staticmethod
    def _clean_synthetic(session, user_id: str) -> None:
        """合成账号的完整清理：档案 + 资质 + 绑定 + 账号行。"""
        from app.models.mysql.platform import (DietitianCustomer, DietitianProfile,
                                               UserProfile)
        from app.models.mysql.user import SysUser
        if not user_id:
            return
        session.query(UserProfile).filter_by(user_id=user_id).delete()
        session.query(DietitianProfile).filter_by(user_id=user_id).delete()
        session.query(DietitianCustomer).filter_by(dietitian_id=user_id).delete()
        session.query(DietitianCustomer).filter_by(customer_id=user_id).delete()
        session.query(SysUser).filter_by(user_id=user_id).delete()

    @staticmethod
    def _clean_record(session, record_id: int) -> None:
        from app.models.mysql.platform import AssessmentReport, AssessmentRecord
        if record_id:
            session.query(AssessmentReport).filter_by(record_id=record_id).delete()
            session.query(AssessmentRecord).filter_by(id=record_id).delete()

    @staticmethod
    def _clean_template(session, code: str) -> None:
        from app.models.mysql.platform import (AssessmentRecord, AssessmentReport,
                                               AssessmentTemplate)
        tpl_ids = [t.id for t in session.query(AssessmentTemplate).filter_by(code=code).all()]
        if tpl_ids:
            rec_ids = [r.id for r in session.query(AssessmentRecord)
                       .filter(AssessmentRecord.template_id.in_(tpl_ids)).all()]
            if rec_ids:
                session.query(AssessmentReport).filter(
                    AssessmentReport.record_id.in_(rec_ids)).delete(synchronize_session=False)
                session.query(AssessmentRecord).filter(
                    AssessmentRecord.id.in_(rec_ids)).delete(synchronize_session=False)
            session.query(AssessmentTemplate).filter(
                AssessmentTemplate.id.in_(tpl_ids)).delete(synchronize_session=False)

    def clean_chat(self, user_id: str) -> None:
        """登记对话数据清理（会话 + 消息）。"""
        self.cleanup.append(lambda s, i=user_id: self._clean_chat(s, i))

    def clean_chat_guest(self, token: str) -> None:
        """登记游客对话清理（游客会话 user_id 为空，按 guest_token 哈希定位）。"""
        from app.services.assessment_service import guest_hash_of
        gh = guest_hash_of(token)
        self.cleanup.append(lambda s, g=gh: self._clean_chat_guest(s, g))

    @staticmethod
    def _clean_chat_guest(session, guest_hash: str) -> None:
        from app.models.mysql.platform import ChatMessage, ChatSession
        sids = [r.id for r in session.query(ChatSession).filter_by(guest_token=guest_hash).all()]
        if sids:
            session.query(ChatMessage).filter(ChatMessage.session_id.in_(sids)).delete(
                synchronize_session=False)
        session.query(ChatSession).filter_by(guest_token=guest_hash).delete()

    @staticmethod
    def _clean_chat(session, user_id: str) -> None:
        from app.models.mysql.platform import ChatMessage, ChatSession
        sids = [r.id for r in session.query(ChatSession).filter_by(user_id=user_id).all()]
        if sids:
            session.query(ChatMessage).filter(ChatMessage.session_id.in_(sids)).delete(
                synchronize_session=False)
        session.query(ChatSession).filter_by(user_id=user_id).delete()

    # ------------------------------------------------------------------ #
    # 管理端
    # ------------------------------------------------------------------ #
    def set_config(self, admin_headers: Dict[str, str], key: str, value: Any) -> Dict[str, Any]:
        """改系统参数，并自动登记还原。

        `scoring.*` / `agent.*` 会影响评分与智能体行为，用例改完必须还原，
        否则同一轮里后面的用例会拿着被改过的参数跑，出现"顺序依赖"的假红。
        """
        old = self.get_config(admin_headers, key)
        if old is not None:
            self.cleanup.append(lambda s, k=key, v=old: self._restore_config(s, k, v))
        # 入参字段名以实现为准：AdminConfigReq = {config_key, config_value, description}
        payload = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return self.c.put(f"{API}/admin/config", headers=admin_headers,
                          json={"config_key": key, "config_value": payload}).json()

    def get_config(self, admin_headers: Dict[str, str], key: str) -> Optional[str]:
        items = (self.c.get(f"{API}/admin/config", headers=admin_headers).json()
                 .get("data") or {}).get("items", [])
        for it in items:
            if it.get("config_key") == key:
                return it.get("config_value")
        return None

    def operation_logs(self, admin_headers: Dict[str, str],
                       action: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"limit": 200}
        if action:
            params["action"] = action
        data = (self.c.get(f"{API}/admin/operation-logs", headers=admin_headers,
                           params=params).json().get("data") or {})
        return data.get("items", [])

    def clean_audit(self, marker: str) -> None:
        """登记审计日志清理：按 detail 里的标记串删。

        `operation_log` 外键列是 operator_id，residue_watchdog 只按 ID 基线回收，
        用例自己产生的日志不会被兜底扫到，必须按业务标记精确删。
        """
        self.cleanup.append(lambda s, m=marker: self._clean_audit(s, m))

    @staticmethod
    def _clean_audit(session, marker: str) -> None:
        from sqlalchemy import text
        # content 是 JSON 列，用 LIKE 匹配其文本形态；target_id 一并覆盖
        session.execute(
            text("DELETE FROM operation_log WHERE CAST(content AS CHAR) LIKE :m"
                 " OR target_id LIKE :m"),
            {"m": f"%{marker}%"})

    @staticmethod
    def _restore_config(session, key: str, value: str) -> None:
        """直接改库还原（不调接口）：清理阶段不依赖 HTTP，且免掉一次审计噪声。

        同时把 `updated_by` 还原成基线账号，否则配置表会永久留着用例账号 ID。
        """
        from sqlalchemy import text
        session.execute(
            text("UPDATE system_config SET config_value=:v, updated_by=:u"
                 " WHERE config_key=:k"),
            {"k": key, "v": value, "u": "UE5D6D3428865"})
        # 评分引擎从库里读权重，若带进程内缓存则必须失效，
        # 否则同一进程里还原后旧权重继续生效，出现"顺序依赖"的假红。
        try:
            from app.services import scoring_engine as se
            eng = getattr(se, "scoring_engine", None)
            if eng is not None and hasattr(eng, "invalidate"):
                eng.invalidate()
        except Exception:                                       # noqa: BLE001
            pass

    # ------------------------------------------------------------------ #
    # 清理登记
    # ------------------------------------------------------------------ #

    def clean_bind(self, dietitian_id: str, customer_id: str) -> None:
        self.cleanup.append(lambda s: self._clean_bind(s, dietitian_id, customer_id))

    def clean_consult(self, consultation_id: int) -> None:
        """登记咨询清理：先删消息再删咨询单。

        `consultation_message` 没有 user_id 列，residue_watchdog 的前缀兜底扫不到，
        必须逐条登记，否则每轮回归都会留下孤儿消息。
        """
        self.cleanup.append(lambda s, i=consultation_id: self._clean_consult(s, i))

    @staticmethod
    def _clean_consult(session, consultation_id: int) -> None:
        from sqlalchemy import text
        session.execute(text("DELETE FROM consultation_message WHERE consultation_id=:i"),
                        {"i": consultation_id})
        session.execute(text("DELETE FROM consultation WHERE id=:i"), {"i": consultation_id})

    @staticmethod
    def _clean_bind(session, dietitian_id: str, customer_id: str) -> None:
        from app.models.mysql.platform import DietitianCustomer
        session.query(DietitianCustomer).filter_by(
            dietitian_id=dietitian_id, customer_id=customer_id).delete()


__all__ = ["ApiHelper", "PASSWORD", "API", "DIMS", "QUESTIONS", "OPTIONS", "ErrCode"]
