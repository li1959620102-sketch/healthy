# -*- coding: utf-8 -*-
"""健康风险测评记录的保存与历史查询。"""
import logging
from typing import Optional

from fastapi import Header, Query, status
from pydantic import BaseModel, Field

from app.exceptions.base_api_exception import AuthenticationException, ValidationException
from app.routers.base_router import BaseRouter
from app.schemas.common import ApiResponse
from app.services.health_assessment_service import HealthAssessmentService
from app.utils.auth_helper import current_user

logger = logging.getLogger(__name__)


class AssessmentSaveRequest(BaseModel):
    """保存测评结果请求"""

    total_score: int = Field(..., description="NRS2002 总分 0-7")
    nutritional_impairment_score: int = Field(0, description="营养受损分 0-3")
    disease_severity_score: int = Field(0, description="疾病严重度分 0-3")
    age_score: int = Field(0, description="年龄分 0 或 1")
    assessment_basis: Optional[str] = Field(None, description="评分依据说明")
    risk_level: Optional[str] = Field(None, description="风险等级，不传则按总分自动判定")
    recommendations: Optional[str] = Field(None, description="健康建议，不传则自动生成")
    assessment_type: Optional[str] = Field("NRS2002", description="测评类型")
    user_name: Optional[str] = Field(None, description="姓名，默认取当前账号")
    sex: Optional[str] = Field(None, description="性别：男/女/其他")
    age: Optional[int] = Field(None, description="年龄")
    bmi: Optional[float] = Field(None, description="BMI 指数")
    weight_change: Optional[str] = Field(None, description="体重变化情况")
    disease_condition: Optional[str] = Field(None, description="疾病状况描述")
    dietary_intake: Optional[str] = Field(None, description="进食情况")

    class Config:
        json_schema_extra = {
            "example": {
                "total_score": 4,
                "nutritional_impairment_score": 2,
                "disease_severity_score": 2,
                "age_score": 0,
                "assessment_basis": "营养受损2分；疾病严重度2分；年龄<70岁0分",
                "bmi": 18.4,
                "weight_change": "近1个月体重下降8%",
                "disease_condition": "2型糖尿病稳定期",
                "dietary_intake": "进食量减少60%",
            }
        }


class AssessmentRouter(BaseRouter):
    """测评记录路由"""

    def __init__(self):
        super().__init__()
        self.router = self._register_routes()

    def _register_routes(self):
        self.router.post(
            "/save",
            response_model=ApiResponse,
            status_code=status.HTTP_200_OK,
            summary="保存健康风险测评结果",
            description="将 NRS2002 测评结果写入 user_health_risk_assessment 表，需登录",
            tags=["健康风险测评记录"],
        )(self.save)

        self.router.get(
            "/list",
            response_model=ApiResponse,
            status_code=status.HTTP_200_OK,
            summary="查询我的测评历史",
            description="按测评时间倒序返回当前登录用户的历史测评记录",
            tags=["健康风险测评记录"],
        )(self.list_history)

        return self.router

    async def save(
        self,
        request: AssessmentSaveRequest,
        authorization: Optional[str] = Header(None, description="Bearer <token>"),
    ) -> ApiResponse:
        try:
            user = current_user(authorization)
            record = HealthAssessmentService.save(user, request.model_dump())
            return ApiResponse(status=200, message="测评结果已保存到健康档案", data=record)
        except AuthenticationException as e:
            return ApiResponse(status=401, message=str(e.detail), data={})
        except ValidationException as e:
            return ApiResponse(status=400, message=str(e.detail), data={})
        except Exception as e:
            logger.error(f"保存测评记录失败：{e}", exc_info=True)
            return ApiResponse(status=500, message=f"保存失败：{e}", data={})

    async def list_history(
        self,
        authorization: Optional[str] = Header(None, description="Bearer <token>"),
        limit: int = Query(50, description="返回条数"),
    ) -> ApiResponse:
        try:
            user = current_user(authorization)
            records = HealthAssessmentService.list_history(user, limit=limit)
            return ApiResponse(status=200, message="success",
                               data={"items": records, "total": len(records)})
        except AuthenticationException as e:
            return ApiResponse(status=401, message=str(e.detail), data={})
        except Exception as e:
            logger.error(f"查询测评历史失败：{e}", exc_info=True)
            return ApiResponse(status=500, message=f"查询失败：{e}", data={})
