# -*- coding: utf-8 -*-
"""B 组 · 用户路由（函数式，依据设计_01 §二 B）。

覆盖范围：当前用户 / 资料维护 / 改密 / 改绑手机（占位）/ 注销（15 日删除）/ 数据导出（占位）。
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.exc import SQLAlchemyError

from app.schemas.platform import ProfileUpdateReq, PasswordUpdateReq, PhoneChangeReq, DataExportReq
from app.services.user_service import UserService
from app.services.dashboard_service import dashboard_service
from app.dao.user_dao import UserDAO
from app.core.responses import success, error
from app.core.errors import ErrCode, BizError
from app.core.deps import get_current_user
from app.utils.security import hash_password, verify_password
from app.utils.db import get_session
from app.models.mysql.user import SysUser
from app.models.mysql.platform import UserProfile, DataDeletionTask, OperationLog

router = APIRouter(prefix="/users", tags=["用户"])


@router.get("/me", summary="当前用户信息", description="返回登录用户基本信息（不含敏感字段）。")
async def me(user: dict = Depends(get_current_user)):
    u = UserDAO.get_by_user_id(user.get("sub"))
    if not u:
        raise BizError(ErrCode.USER_NOT_FOUND, "用户不存在或已停用")
    return success(data=u.to_dict())


@router.put("/me/profile", summary="修改健康档案", description="身高/体重/疾病史/饮食偏好；疾病史第一批暂以 JSON 存储，加密待合规模块接入。")
async def update_profile(
    body: ProfileUpdateReq,
    user: dict = Depends(get_current_user),
):
    user_id = user.get("sub")
    session = get_session()
    try:
        prof = session.query(UserProfile).filter_by(user_id=user_id).first()
        if not prof:
            prof = UserProfile(user_id=user_id)
            session.add(prof)
        if body.height_cm is not None:
            prof.height_cm = body.height_cm
        if body.weight_kg is not None:
            prof.weight_kg = body.weight_kg
        if body.diet_preference is not None:
            prof.diet_preference = body.diet_preference
        if body.diseases is not None:
            # 合规 D1：疾病史属敏感个人信息，须**单独同意**后方可收集。
            # 未勾选（或前端漏传）一律拒绝落库——不靠前端自觉，服务端硬校验。
            # 空列表视为"未填写/清空"，不构成收集行为，无需同意。
            if body.diseases and body.sensitive_consent is not True:
                raise BizError(ErrCode.PARAM_INVALID,
                               "提交疾病史前需单独同意《敏感个人信息处理规则》")
            # TODO（合规）：疾病史属敏感个人信息，接入 AES-256 加密后再落库。
            import json
            prof.diseases_cipher = json.dumps(body.diseases, ensure_ascii=False).encode("utf-8")
            if body.diseases:
                session.add(OperationLog(
                    operator_id=user_id, operator_role=user.get("role", "user"),
                    action="consent.sensitive_info", target_type="user_profile",
                    target_id=user_id,
                    content={"scope": "disease_history", "item_count": len(body.diseases)},
                ))
        session.commit()
        return success(message="档案已更新")
    except SQLAlchemyError as e:
        session.rollback()
        raise BizError(ErrCode.DB_ERROR, f"更新档案失败：{e}")
    finally:
        session.close()


@router.put("/me/password", summary="修改密码", description="校验原密码后更新。")
async def update_password(
    body: PasswordUpdateReq,
    user: dict = Depends(get_current_user),
):
    user_id = user.get("sub")
    session = get_session()
    try:
        u = session.query(SysUser).filter_by(user_id=user_id).first()
        if not u or not verify_password(body.old_password, u.password_hash):
            raise BizError(ErrCode.AUTH_FAILED, "原密码错误")
        u.password_hash = hash_password(body.new_password)
        session.commit()
        return success(message="密码已修改")
    except BizError:
        raise
    except SQLAlchemyError as e:
        session.rollback()
        raise BizError(ErrCode.DB_ERROR, f"修改密码失败：{e}")
    finally:
        session.close()


@router.post("/me/phone/verify", summary="改绑手机号（二次验证）",
             description="向新手机号发送验证码并校验通过后完成改绑；旧号立即失效，可用新号登录。")
async def phone_verify(
    body: PhoneChangeReq,
    user: dict = Depends(get_current_user),
):
    """改绑手机号：二次验证码校验 → 查重 → 落新号密文。

    2026-10-16 由占位实现补全（原实现只回一句"已受理"，手机号根本没换，
    而短信通道已可用——补测用例 IT-A-10 / IT-U-03 直接暴露了这一点）。
    """
    from app.core import rules
    from app.services.sms_service import sms_service
    from app.utils.security import encrypt_phone, phone_hash

    user_id = user.get("sub")
    chk = rules.check_phone(body.phone)
    if not chk.ok:
        raise BizError(ErrCode.PARAM_INVALID, chk.message)
    phone = chk.value

    session = get_session()
    try:
        u = session.query(SysUser).filter_by(user_id=user_id).first()
        if not u:
            raise BizError(ErrCode.USER_NOT_FOUND, "用户不存在")

        ph = phone_hash(phone)
        holder = UserDAO.get_by_phone_hash(ph)
        if holder and holder.user_id != user_id:
            raise BizError(ErrCode.USER_EXISTS, "该手机号已绑定其他账号")

        # 未带验证码 → 参数错误（不静默放行，否则二次验证形同虚设）
        if not (body.verify_code or "").strip():
            raise BizError(ErrCode.PARAM_INVALID, "请输入新手机号收到的短信验证码")
        sms_service.verify_code(phone, body.verify_code)

        old_hash = u.phone_hash
        u.phone_cipher = encrypt_phone(phone)
        u.phone_hash = ph
        session.add(OperationLog(
            operator_id=user_id, operator_role=user.get("role", "user"),
            action="user.phone_change", target_type="user", target_id=user_id,
            content={"phone_changed": bool(old_hash)},
        ))
        session.commit()
        masked = phone[:3] + "****" + phone[-4:]
        return success(message="手机号已改绑", data={"phone_masked": masked})
    except BizError:
        session.rollback()
        raise
    except SQLAlchemyError as e:
        session.rollback()
        raise BizError(ErrCode.DB_ERROR, f"改绑手机号失败：{e}")
    finally:
        session.close()


@router.delete("/me", summary="注销账号", description="软删除并登记 15 日内数据清理任务（合规 D2）。")
async def delete_me(user: dict = Depends(get_current_user)):
    user_id = user.get("sub")
    session = get_session()
    try:
        u = session.query(SysUser).filter_by(user_id=user_id).first()
        if not u:
            raise BizError(ErrCode.USER_NOT_FOUND, "用户不存在")
        u.status = "disabled"
        u.deleted_at = datetime.now()
        # 登记清理任务：注销后 15 日执行
        task = DataDeletionTask(
            user_id=user_id, state="pending",
            scope="mysql,vector,minio",
            execute_at=datetime.now() + timedelta(days=15),
        )
        session.add(task)
        session.add(OperationLog(
            operator_id=user_id, operator_role=user.get("role", "user"),
            action="disable_user", target_type="user", target_id=user_id,
            content={"reason": "self_logout"},
        ))
        session.commit()
        return success(message="账号已注销，相关数据将在 15 日内删除")
    except BizError:
        raise
    except SQLAlchemyError as e:
        session.rollback()
        raise BizError(ErrCode.DB_ERROR, f"注销失败：{e}")
    finally:
        session.close()


@router.post("/me/data-export", summary="导出个人数据",
             description="聚合档案/测评/报告/咨询为 CSV/XLSX，入 MinIO 返回临时签名地址。"
                         "**需短信二次验证**（敏感操作，与改绑手机号同口径）。")
async def data_export(body: DataExportReq, user: dict = Depends(get_current_user)):
    import asyncio
    from app.services.data_export_service import data_export_service
    # 聚合查询 + 文件生成 + MinIO 上传都是同步阻塞调用，放线程池，
    # 否则会卡住整个事件循环（同 prewarm 的处理口径）。
    data = await asyncio.to_thread(data_export_service.export, user, body)
    return success(data=data)


@router.get("/me/data-export/fields", summary="可导出字段清单",
            description="返回各类数据的字段清单，供前端展示导出内容说明（不需二次验证）。")
async def data_export_fields(user: dict = Depends(get_current_user)):
    from app.services.data_export_service import data_export_service
    return success(data={"available": data_export_service.fields()})


@router.get("/me/health-context", summary="健康上下文", description="营养师对话右栏用：体质 / 重点风险 / 忌口 / AI 建议方向（原型营养师智能体右侧栏）。")
async def health_context(user: dict = Depends(get_current_user)):
    return success(data=dashboard_service.health_context(user))
