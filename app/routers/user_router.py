# -*- coding: utf-8 -*-
"""B 组 · 用户路由（函数式，依据设计_01 §二 B）。

覆盖范围：当前用户 / 资料维护 / 改密 / 改绑手机（占位）/ 注销（15 日删除）/ 数据导出（占位）。
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.exc import SQLAlchemyError

from app.schemas.platform import ProfileUpdateReq, PasswordUpdateReq
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
            # TODO（合规）：疾病史属敏感个人信息，接入 AES-256 加密后再落库。
            import json
            prof.diseases_cipher = json.dumps(body.diseases, ensure_ascii=False).encode("utf-8")
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


@router.post("/me/phone/verify", summary="改绑手机号（二次验证）", description="【第一批占位】二次验证码校验，绑定待短信网关接入。")
async def phone_verify(
    phone: str = Body(..., description="新手机号"),
    code: str = Body(..., description="验证码（X-Verify-Code 头或 Body）"),
    user: dict = Depends(get_current_user),
):
    # TODO（第二批）：校验 code 与缓存中的验证码一致后，写 phone_cipher/phone_hash。
    return success(message="手机号改绑已受理（演示占位，待短信网关接入）", data={"phone": phone})


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


@router.get("/me/data-export", summary="导出个人数据", description="【第一批占位】返回可导出字段清单，文件生成待接入。")
async def data_export(user: dict = Depends(get_current_user)):
    # TODO（第二批）：聚合 sys_user / user_profile / assessment_report 等，生成 JSON/PDF。
    return success(data={
        "available": ["profile", "assessments", "reports", "consultations"],
        "note": "数据导出文件生成待接入",
    })


@router.get("/me/health-context", summary="健康上下文", description="营养师对话右栏用：体质 / 重点风险 / 忌口 / AI 建议方向（原型营养师智能体右侧栏）。")
async def health_context(user: dict = Depends(get_current_user)):
    return success(data=dashboard_service.health_context(user))
