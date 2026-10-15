# -*- coding: utf-8 -*-
"""上线安全基线回归测试。"""
import pytest

from app.core.deps import get_current_user
from app.core.errors import BizError, ErrCode
from app.exceptions.base_api_exception import ValidationException
from app.schemas.platform import RegisterReq
from app.services.knowledge_file_service import KnowledgeFileService
from app.services.user_service import UserService
from app.utils.security import create_access_token, create_refresh_token


@pytest.mark.unit
def test_access_token_can_pass_business_auth():
    token = create_access_token("U-SEC-1", "security_user", "user")

    payload = get_current_user(f"Bearer {token}")

    assert payload["sub"] == "U-SEC-1"
    assert payload["type"] == "access"


@pytest.mark.unit
def test_refresh_token_cannot_pass_business_auth():
    token = create_refresh_token("U-SEC-1", "security_user", "user")

    with pytest.raises(BizError) as exc_info:
        get_current_user(f"Bearer {token}")

    assert exc_info.value.code == ErrCode.TOKEN_INVALID


@pytest.mark.unit
def test_public_registration_schema_rejects_privileged_role():
    with pytest.raises(ValueError):
        RegisterReq(username="attacker", password="StrongPass@2025", role="admin")


@pytest.mark.unit
def test_user_service_rejects_privileged_role_before_database_access(monkeypatch):
    monkeypatch.setattr(
        "app.services.user_service.UserDAO.get_by_username",
        lambda _username: pytest.fail("角色校验必须先于数据库访问"),
    )

    with pytest.raises(ValidationException):
        UserService.register("attacker", "StrongPass@2025", role="admin")


@pytest.mark.unit
def test_embedding_failure_is_explicit_instead_of_random(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise TimeoutError("向量服务超时")

    monkeypatch.setattr("app.services.knowledge_file_service.requests.post", _raise)
    service = KnowledgeFileService.__new__(KnowledgeFileService)
    service.ollama_base_url = "http://embedding.invalid"
    service.embedding_model = "test-model"

    with pytest.raises(RuntimeError, match="禁止使用随机向量污染知识库"):
        service._generate_embedding("测试文本")
