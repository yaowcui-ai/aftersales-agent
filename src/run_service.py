import asyncio
import logging
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from core import settings
from core.settings import DatabaseType

load_dotenv()

if __name__ == "__main__":
    root_logger = logging.getLogger()
    if root_logger.handlers:
        print(
            f"Warning: Root logger already has {len(root_logger.handlers)} handler(s) configured. "
            f"basicConfig() will be ignored. Current level: {logging.getLevelName(root_logger.level)}"
        )

    logging.basicConfig(level=settings.LOG_LEVEL.to_logging_level())
    windows_mcp = sys.platform == "win32" and settings.DATABASE_TYPE == DatabaseType.MYSQL
    reload_enabled = settings.is_dev() and not windows_mcp
    # A reloader/worker parent passes its listener to child processes. Reusing
    # that listener with another Proactor IOCP fails with WinError 87 on Windows.
    # MCP needs Proactor subprocess support, so keep one listener-owning process.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
            if windows_mcp
            else asyncio.WindowsSelectorEventLoopPolicy()
        )
    if windows_mcp:
        logging.getLogger(__name__).info(
            "Windows MySQL/MCP runtime: one worker, auto-reload disabled. "
            "Restart the Python service after code changes."
        )
    uvicorn.run(
        "service:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=reload_enabled,
        reload_dirs=[str(Path(__file__).resolve().parent)] if reload_enabled else None,
        loop="asyncio" if windows_mcp else "auto",
        workers=1 if windows_mcp else None,
        timeout_graceful_shutdown=settings.GRACEFUL_SHUTDOWN_TIMEOUT,
    )
