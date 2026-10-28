import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_mcp_business_rejection_keeps_error_code_after_session_cleanup(tmp_path, monkeypatch):
    from aftersales_delivery import transport
    from aftersales_delivery.business import BusinessError, Store
    from aftersales_delivery.config import Config
    from aftersales_delivery.db import initialize
    from aftersales_delivery.mcp_server import create_app

    config = Config(f"sqlite+aiosqlite:///{tmp_path / 'mcp.sqlite'}", "key" * 16, {}, mcp_url="http://test/mcp")
    engine = create_async_engine(config.db_url)
    await initialize(engine)
    action = await Store(engine).prepare("learner-001", "unapproved", "refund", "DEMO-004", "x", "1.00")
    app = create_app(config)

    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), headers=headers, timeout=timeout, auth=auth)

    monkeypatch.setattr(transport, "http_client", factory)
    try:
        async with app.router.lifespan_context(app):
            with pytest.raises(BusinessError) as error:
                await transport.call_tool(config, "learner-001", "execute_action",
                    {"action_id": action["action_id"]}, action_id=action["action_id"])
            assert error.value.code == "NOT_APPROVED"
    finally:
        await engine.dispose()
