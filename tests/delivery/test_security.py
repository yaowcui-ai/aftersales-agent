import time

import pytest


def security():
    from aftersales_delivery import security

    return security


def test_password_hash_uses_salt_and_rejects_wrong_password():
    module = security()
    first = module.hash_password("test-only-password")
    second = module.hash_password("test-only-password")
    assert first != second
    assert module.verify_password("test-only-password", first)
    assert not module.verify_password("wrong", first)
    assert not module.verify_password("anything", "broken")


def test_token_signature_expiry_and_purpose_are_enforced():
    module = security()
    token = module.issue_token("secret-a" * 8, "customer1", "learner-001", "customer")
    claims = module.decode_token(token, "secret-a" * 8)
    assert claims["customer_id"] == "learner-001"
    for key, purpose in [("wrong-key" * 8, "session"), ("secret-a" * 8, "mcp")]:
        with pytest.raises(module.AuthenticationError):
            module.decode_token(token, key, purpose=purpose)
    expired = module.issue_token("secret-a" * 8, "customer1", "learner-001", "customer", ttl=-1)
    with pytest.raises(module.AuthenticationError):
        module.decode_token(expired, "secret-a" * 8)


def test_signed_mcp_context_binds_customer_and_one_execution_action():
    module = security()
    token = module.issue_token(
        "key" * 16,
        "reviewer",
        "learner-002",
        "approver",
        purpose="mcp",
        action_id="a" * 32,
        ttl=60,
    )
    claims = module.decode_token(token, "key" * 16, purpose="mcp")
    assert claims["action_id"] == "a" * 32
    assert claims["exp"] <= time.time() + 61
    with pytest.raises(module.AuthenticationError):
        module.decode_token(token, "key" * 16)


@pytest.mark.parametrize("token", ["", "a.b.c", "not-a-token", "a." + "x" * 20000])
def test_malformed_tokens_are_constant_public_authentication_errors(token):
    module = security()
    with pytest.raises(module.AuthenticationError, match="INVALID_TOKEN"):
        module.decode_token(token, "key" * 16)
