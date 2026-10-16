# -*- coding: utf-8 -*-
"""E 组 · 测评服务（发起 / 草稿 / 提交 / 作废 / 游客认领）。

落地需求与评审的关键约定：
- C2 模板版本锁定：发起时把 template_version 写死在记录上，模板后续改版不影响本次测评。
- C6 画像快照：发起时快照 user_profile，中途改资料不导致分数漂移。
- B6 草稿保留 30 天：expire_at。
- A8 草稿全程保留：任何异常都不让用户重做，答案实时落库。
- 评审 M3 越权返回 404（RESOURCE_NOT_FOUND），避免通过响应差异枚举他人记录。
- 幂等：30 秒内同 Idempotency-Key 重复提交返回首次结果，不重复生成报告。
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.core import rules
from app.core.errors import BizError, ErrCode
from app.dao.assessment_dao import RecordDAO, ReportDAO, TemplateDAO, session_scope
from app.models.mysql.platform import AssessmentReport, UserProfile
from app.services.scoring_engine import scoring_engine
from app.utils.db import get_session
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger("AssessmentService")

DRAFT_EXPIRE_DAYS = rules.DRAFT_TTL_DAYS


def guest_hash_of(token: Optional[str]) -> Optional[str]:
    """游客令牌 → 归属哈希（令牌本身不入库，防泄露后被冒用认领）。"""
    if not token:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:64]


def _questions_of(template: Any) -> list:
    """兼容 question_config 为 list 或 {"questions": [...]}。"""
    qc = getattr(template, "question_config", None)
    if isinstance(qc, dict):
        return qc.get("questions") or []
    return qc or []


def _profile_snapshot(user_id: Optional[str]) -> Dict[str, Any]:
    """发起时快照用户画像；疾病史为密文，不入快照（合规）。"""
    if not user_id:
        return {}
    session = get_session()
    try:
        p = session.query(UserProfile).filter_by(user_id=user_id).first()
        if not p:
            return {}
        snap: Dict[str, Any] = {
            "height_cm": p.height_cm,
            "weight_kg": float(p.weight_kg) if p.weight_kg is not None else None,
            "diet_preference": p.diet_preference,
        }
        try:
            if p.height_cm and p.weight_kg and float(p.height_cm) > 0:
                h = float(p.height_cm) / 100
                snap["bmi"] = round(float(p.weight_kg) / (h * h), 1)
        except (TypeError, ValueError):
            pass
        return snap
    except Exception as e:
        logger.warning(f"读取用户画像失败（快照为空，不阻断发起）：{e}")
        return {}
    finally:
        session.close()


def _assert_owner(rec, user: Optional[dict], guest_hash: Optional[str]) -> None:
    """归属校验：本人或本人游客记录；否则按评审 M3 返回 404 防枚举。"""
    if rec is None:
        raise BizError(ErrCode.ASSESSMENT_NOT_FOUND, "测评记录不存在")
    if rec.user_id:
        if not user or str(rec.user_id) != str(user.get("sub")):
            raise BizError(ErrCode.RESOURCE_NOT_FOUND, "测评记录不存在")
    else:
        if not guest_hash or rec.guest_token != guest_hash:
            raise BizError(ErrCode.RESOURCE_NOT_FOUND, "测评记录不存在")


class AssessmentService:
    # ------------------------------------------------------------------ #
    @staticmethod
    def start_assessment(user: Optional[dict], template_id: int,
                         template_version: Optional[int] = None,
                         guest_hash: Optional[str] = None) -> Dict[str, Any]:
        """发起测评：锁定模板版本 + 画像快照，返回题目与快照。"""
        with session_scope() as s:
            tpl = TemplateDAO.get_published(template_id, session=s)
            if not tpl:
                raise BizError(ErrCode.TEMPLATE_NOT_FOUND, "模板不存在或未发布")
            if template_version is not None and int(template_version) != int(tpl.version):
                raise BizError(ErrCode.TEMPLATE_VERSION_CONFLICT, "模板版本已更新，请刷新后重试")

            # BV-21 游客配额：游客免费测评有次数上限，达上限引导注册
            if guest_hash and RecordDAO.count_guest_completed(guest_hash, session=s) >= rules.GUEST_ASSESSMENT_QUOTA:
                raise BizError(ErrCode.GUEST_NOT_ALLOWED,
                               "游客免费测评次数已用完，注册后可解锁全部功能")

            uid = user.get("sub") if user and user.get("role") != "guest" else None

            # IT-AS-13：同模板已有未完成测评时**不新建**，把那一份原样还给前端，
            # 由前端引导「继续作答」（用例允许"引导继续作答"这一实现口径）。
            # 若不拦，用户会在两份草稿之间来回切换，答案各存一半、进度也随之混乱。
            existing = RecordDAO.find_in_progress(tpl.id, user_id=uid,
                                                  guest_hash=guest_hash, session=s)
            if existing is not None:
                return AssessmentService._start_payload(tpl, existing, resumed=True)

            rec = RecordDAO.create({
                "user_id": uid,
                "guest_token": guest_hash,
                "template_id": tpl.id,
                "template_version": int(tpl.version),
                "profile_snapshot": _profile_snapshot(user.get("sub") if user else None),
                "status": "in_progress",
                "answers": {},
                "current_page": 1,
                "started_at": datetime.now(),
                "expire_at": datetime.now() + timedelta(days=DRAFT_EXPIRE_DAYS),
            }, session=s)

            return AssessmentService._start_payload(tpl, rec, resumed=False)

    @staticmethod
    def _start_payload(tpl: Any, rec: Any, resumed: bool = False) -> Dict[str, Any]:
        """发起测评的统一响应（新建与"继续作答"共用，避免两处结构漂移）。"""
        return {
            "assessment_id": rec.id,
            "template_snapshot": {
                "template_id": tpl.id,
                "version": int(tpl.version),
                "code": tpl.code,
                "name": tpl.name,
                "type": tpl.type,
                "dimensions": tpl.dimension_config or [],
                "questions": _questions_of(tpl),
            },
            "profile_snapshot": rec.profile_snapshot,
            "status": rec.status,
            # 复用时前端据此提示"上次答到第 N 题，是否继续"，而不是当成新测评
            "resumed": resumed,
            "saved_at": rec.last_saved_at,
            "expire_at": rec.expire_at,
        }

    # ------------------------------------------------------------------ #
    @staticmethod
    def get_detail(record_id: int, user: Optional[dict],
                   guest_hash: Optional[str] = None) -> Dict[str, Any]:
        with session_scope() as s:
            rec = RecordDAO.get(record_id, session=s)
            _assert_owner(rec, user, guest_hash)
            tpl = TemplateDAO.get(rec.template_id, session=s)
            questions = _questions_of(tpl) if tpl else []
            answered = rec.answers or {}
            return {
                "assessment_id": rec.id,
                "template_id": rec.template_id,
                "template_version": rec.template_version,
                "status": rec.status,
                "answers": answered,
                "current_page": rec.current_page,
                "progress": rec.progress or f"{len(answered)}/{len(questions)}",
                "questions": questions,
                "dimensions": (tpl.dimension_config if tpl else []),
                "profile_snapshot": rec.profile_snapshot,
                # 多端续答（IT-AS-03）与草稿乐观锁（IT-AS-18）都靠这个值：
                # B 设备读到的 saved_at 原样回传，服务端才能判断草稿是否被人改过。
                "saved_at": rec.last_saved_at,
                "expire_at": rec.expire_at,
                "submitted_at": rec.submitted_at,
            }

    # ------------------------------------------------------------------ #
    @staticmethod
    def save_draft(record_id: int, user: Optional[dict], guest_hash: Optional[str],
                   answers: Any, current_page: Optional[int] = None,
                   expect_saved_at: Optional[datetime] = None) -> Dict[str, Any]:
        with session_scope() as s:
            rec = RecordDAO.get(record_id, session=s)
            _assert_owner(rec, user, guest_hash)
            if rec.status != "in_progress":
                raise BizError(ErrCode.PARAM_INVALID, "测评已提交或作废，无法继续作答")
            # BV-24 草稿有效期：过期草稿先作废并提交（否则抛出异常会被事务回滚），再拒绝续答
            if rec.expire_at and rec.expire_at < datetime.now():
                RecordDAO.void(record_id, session=s)
                s.commit()
                raise BizError(ErrCode.PARAM_INVALID, "草稿已过期，请重新发起测评")

            tpl = TemplateDAO.get(rec.template_id, session=s)
            questions = _questions_of(tpl)
            total = len(questions) if tpl else 0
            merged = dict(rec.answers or {})
            incoming = scoring_engine.normalize_answers(answers) or {}
            for k, v in incoming.items():
                merged[k] = v
            progress = f"{len(merged)}/{total}" if total else f"{len(merged)}"

            # IT-AS-05 / 需求 A6：本次改动的答案本地即时校验，越界只提示不阻断保存。
            # 只校验「这次提交的题」，避免把历史遗留的越界答案反复报给用户。
            warnings = rules.answer_warnings(questions, incoming)

            updated = RecordDAO.save_draft(
                record_id, merged, current_page, progress,
                expect_saved_at=expect_saved_at, session=s,
            )
            if updated is None:
                # IT-AS-18 / 评审 M7 草稿乐观锁：saved_at 对不上说明另一端已写入，
                # 此时**不能**用本地草稿覆盖远端（会把另一台设备刚答的题抹掉）。
                # 用例原文写的是 HTTP 409；本项目基线是「HTTP 恒 200 + 业务码」，
                # 故以 ASSESSMENT_CONFLICT(50004) 表达同一语义。
                raise BizError(ErrCode.ASSESSMENT_CONFLICT,
                               "草稿已在其他设备更新，请刷新后重试")
            return {"saved_at": updated.last_saved_at, "progress": updated.progress,
                    "warnings": warnings}

    # ------------------------------------------------------------------ #
    @staticmethod
    @staticmethod
    def _sync_profile_from_answers(tpl, answers, user_id: str, session=None) -> None:
        """把档案题（dimension=profile 的多选）同步进 user_profile。

        只认规则表 rules.ALLERGY_KEYWORDS / TABOO_KEYWORDS 里登记的标签，
        其余选项值忽略（防止脏值进过滤链）。存储 JSON：{labels, keywords}。
        """
        import json

        from app.models.mysql.platform import UserProfile
        from app.core import rules as _rules

        # rec.answers 落库为 JSON 字符串，这里兼容 dict / str
        if isinstance(answers, str):
            try:
                answers = json.loads(answers)
            except Exception:
                answers = {}
        ans = answers if isinstance(answers, dict) else {}
        qs = tpl.question_config or []
        allergies, taboo = [], []
        for q in qs:
            if not isinstance(q, dict) or q.get("dimension") != "profile":
                continue
            qid = str(q.get("question_id") or "")
            chosen = ans.get(qid)
            if isinstance(chosen, str):
                chosen = [chosen]
            if not isinstance(chosen, list):
                continue
            table = _rules.ALLERGY_KEYWORDS if "过敏" in str(q.get("title") or "") else _rules.TABOO_KEYWORDS
            target = allergies if table is _rules.ALLERGY_KEYWORDS else taboo
            for label in chosen:
                if label in ("无过敏", "无忌口") or label not in table:
                    continue
                if label not in target:
                    target.append(label)

        payload_a = json.dumps({"labels": allergies, "keywords": sorted(
            {k for lab in allergies for k in _rules.ALLERGY_KEYWORDS.get(lab, [])})}, ensure_ascii=False) if allergies else None
        payload_t = json.dumps({"labels": taboo, "keywords": sorted(
            {k for lab in taboo for k in _rules.TABOO_KEYWORDS.get(lab, [])})}, ensure_ascii=False) if taboo else None

        prof = session.query(UserProfile).filter_by(user_id=user_id).first()
        if not prof:
            prof = UserProfile(user_id=user_id)
            session.add(prof)
        if payload_a is not None or payload_t is not None:
            if payload_a is not None:
                prof.allergies_json = payload_a
            if payload_t is not None:
                prof.taboo_json = payload_t
        session.flush()

    def submit(record_id: int, user: Optional[dict], guest_hash: Optional[str],
               idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """提交测评：规则引擎同步算分并落库，分析文本由 SSE 通道渐进返回。"""
        with session_scope() as s:
            # 加行锁读取：同一测评的并发提交在此串行化（BV-29），
            # 避免多个请求同时判定"报告不存在"而并发 INSERT 撞 uk_record。
            rec = RecordDAO.get_for_update(record_id, session=s)
            _assert_owner(rec, user, guest_hash)

            # IT-AS-11 / 设计_01 §E：Idempotency-Key 必填。缺键时**明确拒绝**而不是
            # 静默放行——否则用户在网络抖动下连点两次提交，就会真的生成两份报告，
            # 而"提交"这一步还会重置分析文本、重新调用模型，代价不可逆。
            if not idempotency_key or not str(idempotency_key).strip():
                raise BizError(ErrCode.PARAM_INVALID,
                               "缺少 Idempotency-Key 请求头，无法保证重复提交的安全性")

            # 幂等（BV-23）：同键 30 秒内重复提交返回首次结果；超出窗口按新一次提交处理。
            # 键上有全局唯一索引，先全局查再写，避免 1062 → 500。
            prev = RecordDAO.get_by_idempotency(idempotency_key, session=s)
            if prev is not None and prev.id != rec.id:
                raise BizError(ErrCode.PARAM_INVALID,
                               "幂等键已被其他测评占用，请更换 Idempotency-Key")
            if (prev is not None and prev.id == rec.id and prev.status == "completed"
                    and AssessmentService._within_idempotency_window(prev)):
                prev_rep = ReportDAO.get_by_record(prev.id, session=s)
                if prev_rep:
                    return AssessmentService._submit_result(prev, prev_rep, idempotent=True)

            if rec.status == "void":
                raise BizError(ErrCode.PARAM_INVALID, "测评已作废，无法提交")
            # IT-AS-12 / IT-AS-10：已提交的测评不得再次走提交路径。
            # 重复提交若放行，会重置报告分析文本并重新调用模型（用户已看到的报告会"变空"）；
            # 「重新生成分析」是独立动作，走 /report/regenerate（IT-R-08）。
            if rec.status == "completed":
                raise BizError(ErrCode.ASSESSMENT_CONFLICT,
                               "测评已提交，无需重复提交；如需重新生成分析请在报告页点击「重新生成」")
            # BV-24 草稿有效期：过期草稿先作废并提交，再拒绝提交
            if rec.expire_at and rec.expire_at < datetime.now():
                RecordDAO.void(record_id, session=s)
                s.commit()
                raise BizError(ErrCode.PARAM_INVALID, "草稿已过期，请重新发起测评")
            if not rec.answers:
                raise BizError(ErrCode.PARAM_INVALID, "答案不能为空")

            tpl = TemplateDAO.get(rec.template_id, session=s)
            if not tpl:
                raise BizError(ErrCode.TEMPLATE_NOT_FOUND, "模板不存在")

            # IT-AS-06 / BV-11 必答校验：回传**完整缺失清单**，前端可一次性高亮，
            # 而不是"改一题被拒一次"。
            missing = rules.missing_required_questions(_questions_of(tpl), rec.answers)
            if missing:
                raise BizError(ErrCode.PARAM_INVALID,
                               f"还有 {len(missing)} 道必答题未作答，请补全后提交",
                               data={"missing": missing})

            result = scoring_engine.score(tpl, rec.answers)

            # 档案题（dimension=profile，不计分）：把过敏/忌口选择写入健康档案。
            # 游客（user_id 为空）跳过；失败不影响提交本身。
            if rec.user_id:
                try:
                    AssessmentService._sync_profile_from_answers(tpl, rec.answers, rec.user_id, session=s)
                except Exception as e:
                    logger.warning(f"档案题同步健康档案失败（不影响提交）：{e}")

            rep = ReportDAO.get_by_record(rec.id, session=s)
            payload = {
                "record_id": rec.id,
                "user_id": rec.user_id,
                "template_id": tpl.id,
                "template_version": rec.template_version,
                "state": "generating",
                "score": result["score"],
                "standardized_score": result["standardized_score"],
                "level": result["level"],
                "dimension_scores": result["dimension_scores"],
                "suggestions": result["suggestions"],
                # 评审 M6：记录生成当时使用的评分规则版本，便于事后追溯
                "scoring_version": result.get("scoring_version"),
                "disclaimer": "本内容由 AI 生成，不构成诊疗建议",
            }
            if rep:
                for k, v in payload.items():
                    setattr(rep, k, v)
                rep.retry_count = 0
                rep.analysis_text = None
                RecordDAO.mark_submitted(rec.id, idempotency_key, session=s)
                return AssessmentService._submit_result(rec, rep)

            try:
                rep = ReportDAO.create(payload, session=s)
            except IntegrityError:
                # 兜底：多进程部署下行锁不跨进程生效时，仍可能撞 assessment_report.uk_record。
                # 此时回滚并复用已落库的那一份，保证"并发只产生一份报告"（BV-29）。
                s.rollback()
                exist = ReportDAO.get_by_record(record_id, session=s)
                if exist is None:
                    raise
                logger.warning(f"并发提交撞唯一键，复用已有报告 report_id={exist.id}")
                return AssessmentService._submit_result(rec, exist, idempotent=True)

            RecordDAO.mark_submitted(rec.id, idempotency_key, session=s)
            return AssessmentService._submit_result(rec, rep)

    @staticmethod
    def _within_idempotency_window(rec, now: Optional[datetime] = None) -> bool:
        """BV-23：同键在 30 秒窗口内视为幂等；超窗口按新一次提交处理。

        以 `submitted_at` 为准；缺失时间戳时保守判定为窗口内（保证首次结果可复用）。
        """
        ts = getattr(rec, "submitted_at", None)
        if not ts:
            return True
        return ((now or datetime.now()) - ts).total_seconds() <= rules.IDEMPOTENCY_WINDOW_SECONDS

    @staticmethod
    def _submit_result(rec, rep, idempotent: bool = False) -> Dict[str, Any]:
        return {
            "assessment_id": rec.id,
            "report_id": rep.id,
            "status": rec.status,
            "score": float(rep.score) if rep.score is not None else None,
            "level": rep.level,
            "dimension_scores": rep.dimension_scores or [],
            "idempotent": idempotent,
            "generation": {
                "state": rep.state,
                "channel": f"/api/v1/assessments/{rec.id}/report/stream",
            },
        }

    # ------------------------------------------------------------------ #
    @staticmethod
    def cancel(record_id: int, user: Optional[dict], guest_hash: Optional[str] = None) -> Dict[str, Any]:
        with session_scope() as s:
            rec = RecordDAO.get(record_id, session=s)
            _assert_owner(rec, user, guest_hash)
            if rec.status == "completed":
                raise BizError(ErrCode.PARAM_INVALID, "已提交的测评不能作废")
            RecordDAO.void(record_id, session=s)
            return {"assessment_id": rec.id, "status": "void"}

    # ------------------------------------------------------------------ #
    @staticmethod
    def list_mine(user: Optional[dict], status: Optional[str] = None,
                  limit: int = 50) -> Dict[str, Any]:
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录")
        with session_scope() as s:
            rows = RecordDAO.list_by_user(user.get("sub"), status=status, limit=limit, session=s)
            items = []
            for r in rows:
                rep = ReportDAO.get_by_record(r.id, session=s)
                items.append({
                    "assessment_id": r.id,
                    "template_id": r.template_id,
                    "template_version": r.template_version,
                    "status": r.status,
                    "progress": r.progress,
                    "started_at": r.started_at,
                    "submitted_at": r.submitted_at,
                    "report_id": rep.id if rep else None,
                    "score": float(rep.score) if rep and rep.score is not None else None,
                    "level": rep.level if rep else None,
                })
            return {"items": items, "total": len(items)}

    # ------------------------------------------------------------------ #
    @staticmethod
    def claim_guest(user: Optional[dict], guest_token: Optional[str],
                    phone: Optional[str] = None,
                    verify_code: Optional[str] = None) -> Dict[str, Any]:
        """游客数据认领（评审 M3）：仅 7 天内、未绑定账号的游客记录。

        为什么仅凭 `guest_token` 不够（设计评审 M2）：
        游客令牌存在浏览器本地，共用设备、XSS 或日志泄漏都可能让它落到别人手里，
        仅凭令牌即可把他人健康测评数据认领到自己账号下。因此要求**同时**
        通过手机号短信验证码校验，证明操作者能控制一个真实手机号，
        并在服务端留下可追溯的验证凭据。
        """
        if not user:
            raise BizError(ErrCode.UNAUTHENTICATED, "请先登录后再认领")
        if not guest_token:
            raise BizError(ErrCode.PARAM_INVALID, "缺少 guest_token")
        if not phone or not verify_code:
            raise BizError(ErrCode.PARAM_INVALID,
                           "认领游客数据需提供手机号与验证码，防止令牌被冒用")
        from app.services.sms_service import sms_service
        sms_service.verify_code(phone, verify_code)      # 不通过则抛 20003，认领终止

        gh = guest_hash_of(guest_token)
        with session_scope() as s:
            count = RecordDAO.claim(gh, str(user.get("sub")), days=rules.CLAIM_WINDOW_DAYS, session=s)
            return {"claimed_assessments": count, "claimed_consultations": 0,
                    "verified": True}

    # ------------------------------------------------------------------ #
    @staticmethod
    def regenerate_report(record_id: int, user: Optional[dict],
                          guest_hash: Optional[str] = None) -> Dict[str, Any]:
        """重新生成报告：保留评分（规则算，确定性），重置文本状态。"""
        with session_scope() as s:
            rec = RecordDAO.get(record_id, session=s)
            _assert_owner(rec, user, guest_hash)
            rep = ReportDAO.get_by_record(rec.id, session=s)
            if not rep:
                raise BizError(ErrCode.ASSESSMENT_NOT_FOUND, "报告不存在，请先提交测评")
            rep.state = "generating"
            rep.analysis_text = None
            rep.retry_count = 0
            return {
                "assessment_id": rec.id,
                "report_id": rep.id,
                "generation": {
                    "state": rep.state,
                    "channel": f"/api/v1/assessments/{rec.id}/report/stream",
                },
            }
