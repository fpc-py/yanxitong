"""认证与匿名配额单元测试（不依赖 MySQL / LLM）。

覆盖：口令哈希与校验、用户名/密码规则、令牌格式与 Bearer 解析、
注册/登录流程（内存版 store）、配额计数与用尽、存储不可用时的降级。
"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api.deps import _anonymous_id, extract_bearer_token
from src.auth.service import (
    AuthError,
    AuthService,
    generate_token,
    hash_password,
    validate_credentials,
    verify_password,
)
from src.auth.store import AuthStoreUnavailable
from src.core.config import get_settings


class FakeStore:
    """内存版 AuthStore：镜像真实 store 的 consume 语义（used 为消费后的值）。"""

    def __init__(self) -> None:
        self.users: dict[str, dict] = {}
        self.tokens: dict[str, int] = {}
        self.anon: dict[str, int] = {}
        self.unavailable = False
        self._next_id = 1

    def _guard(self) -> None:
        if self.unavailable:
            raise AuthStoreUnavailable("fake store down")

    async def create_user(self, username: str, password_hash: str):
        self._guard()
        if username in self.users:
            return None
        uid = self._next_id
        self._next_id += 1
        self.users[username] = {"id": uid, "username": username, "password_hash": password_hash}
        return uid

    async def get_user_by_username(self, username: str):
        self._guard()
        return self.users.get(username)

    async def touch_last_login(self, user_id: int) -> None:
        self._guard()

    async def create_token(self, user_id: int, token: str, ttl_days: int) -> None:
        self._guard()
        self.tokens[token] = user_id

    async def get_user_by_token(self, token: str):
        self._guard()
        uid = self.tokens.get(token)
        if uid is None:
            return None
        for u in self.users.values():
            if u["id"] == uid:
                return {"id": uid, "username": u["username"], "created_at": None}
        return None

    async def delete_token(self, token: str) -> int:
        self._guard()
        return 1 if self.tokens.pop(token, None) is not None else 0

    async def get_anon_used(self, anon_id: str) -> int:
        self._guard()
        return self.anon.get(anon_id, 0)

    async def consume_anon_quota(self, anon_id: str, limit: int):
        self._guard()
        used = self.anon.get(anon_id, 0)
        if used >= limit:
            return False, used
        self.anon[anon_id] = used + 1
        return True, used + 1


def _service() -> tuple[AuthService, FakeStore]:
    store = FakeStore()
    return AuthService(store=store), store


class TestPasswordHashing:
    def test_roundtrip(self):
        stored = hash_password("s3cret-密码")
        assert stored.startswith("pbkdf2_sha256$")
        assert "s3cret-密码" not in stored
        assert verify_password("s3cret-密码", stored) is True
        assert verify_password("wrong", stored) is False

    def test_malformed_stored_hash(self):
        assert verify_password("x", "not-a-valid-hash") is False
        assert verify_password("x", "") is False

    def test_salts_are_unique(self):
        assert hash_password("same") != hash_password("same")


class TestToken:
    def test_token_format(self):
        t = generate_token()
        assert len(t) == 64
        int(t, 16)  # 全部为十六进制字符

    def test_extract_bearer(self):
        good = "a1" * 32
        assert extract_bearer_token(SimpleNamespace(headers={"authorization": f"Bearer {good}"})) == good
        assert extract_bearer_token(SimpleNamespace(headers={})) == ""
        assert extract_bearer_token(SimpleNamespace(headers={"authorization": "Bearer short"})) == ""
        assert extract_bearer_token(SimpleNamespace(headers={"authorization": "Basic abc"})) == ""

    def test_anonymous_id(self):
        req = SimpleNamespace(headers={"x-anon-id": "browser-1.abC_9"}, client=SimpleNamespace(host="1.2.3.4"))
        assert _anonymous_id(req) == "browser-1.abC_9"
        # 非法字符回退到 IP
        bad = SimpleNamespace(headers={"x-anon-id": "bad id!"}, client=SimpleNamespace(host="1.2.3.4"))
        assert _anonymous_id(bad) == "ip-1.2.3.4"
        # 缺少 header 也回退到 IP
        none = SimpleNamespace(headers={}, client=SimpleNamespace(host="5.6.7.8"))
        assert _anonymous_id(none) == "ip-5.6.7.8"


class TestValidation:
    def test_username_rules(self):
        validate_credentials("张三-abc_1", "123456")
        for bad in ["a", " bad", "bad name", "x" * 33, "name!"]:
            with pytest.raises(AuthError) as ei:
                validate_credentials(bad, "123456")
            assert ei.value.code == "INVALID_USERNAME"
            assert ei.value.status_code == 422

    def test_password_rules(self):
        with pytest.raises(AuthError) as ei:
            validate_credentials("user1", "123")
        assert ei.value.code == "INVALID_PASSWORD"
        assert ei.value.status_code == 422


class TestRegisterLogin:
    def test_register_then_login(self):
        svc, _ = _service()
        reg = asyncio.run(svc.register("researcher1", "secret6"))
        assert reg["user"]["username"] == "researcher1"
        assert len(reg["token"]) == 64

        again = asyncio.run(svc.login("researcher1", "secret6"))
        assert again["user"]["id"] == reg["user"]["id"]
        assert again["token"] != reg["token"]

    def test_duplicate_username(self):
        svc, _ = _service()
        asyncio.run(svc.register("dup-user", "secret6"))
        with pytest.raises(AuthError) as ei:
            asyncio.run(svc.register("dup-user", "another6"))
        assert ei.value.code == "USERNAME_TAKEN"
        assert ei.value.status_code == 409

    def test_wrong_password(self):
        svc, _ = _service()
        asyncio.run(svc.register("pw-user", "secret6"))
        with pytest.raises(AuthError) as ei:
            asyncio.run(svc.login("pw-user", "wrong66"))
        assert ei.value.code == "BAD_CREDENTIALS"
        assert ei.value.status_code == 401

    def test_resolve_and_logout(self):
        svc, _ = _service()
        reg = asyncio.run(svc.register("tok-user", "secret6"))
        token = reg["token"]
        assert asyncio.run(svc.resolve_token(token))["username"] == "tok-user"
        asyncio.run(svc.logout(token))
        assert asyncio.run(svc.resolve_token(token)) is None

    def test_store_down_returns_503(self):
        svc, store = _service()
        store.unavailable = True
        with pytest.raises(AuthError) as ei:
            asyncio.run(svc.register("down-user", "secret6"))
        assert ei.value.code == "AUTH_UNAVAILABLE"
        assert ei.value.status_code == 503


class TestAnonymousQuota:
    def test_consume_until_exhausted(self):
        svc, _ = _service()
        limit = get_settings().auth.anonymous_limit
        for i in range(limit):
            remaining = asyncio.run(svc.consume_question("anon-a"))
            assert remaining == limit - i - 1
        with pytest.raises(AuthError) as ei:
            asyncio.run(svc.consume_question("anon-a"))
        assert ei.value.code == "QUOTA_EXCEEDED"
        assert ei.value.status_code == 403

    def test_quotas_are_per_identity(self):
        svc, _ = _service()
        asyncio.run(svc.consume_question("anon-a"))
        asyncio.run(svc.consume_question("anon-a"))
        assert asyncio.run(svc.consume_question("anon-b")) == get_settings().auth.anonymous_limit - 1

    def test_peek_quota(self):
        svc, _ = _service()
        limit = get_settings().auth.anonymous_limit
        info = asyncio.run(svc.peek_quota("anon-peek"))
        assert info == {"limit": limit, "used": 0, "remaining": limit}
        asyncio.run(svc.consume_question("anon-peek"))
        info = asyncio.run(svc.peek_quota("anon-peek"))
        assert info["used"] == 1
        assert info["remaining"] == limit - 1

    def test_fail_open_when_store_down(self):
        """MySQL 不可用时配额检查放行（演示可继续），peek 返回 None。"""
        svc, store = _service()
        store.unavailable = True
        assert asyncio.run(svc.consume_question("anon-a")) is None
        assert asyncio.run(svc.peek_quota("anon-a")) is None
