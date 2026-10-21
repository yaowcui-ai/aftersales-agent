"""Read only project-owned configuration; never inspect another project's secrets."""

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[2]
DATABASE = "aftersales_delivery"


@dataclass(frozen=True)
class Config:
    db_url: str
    signing_key: str
    accounts: dict
    mcp_url: str = "http://127.0.0.1:8091/mcp"
    checkpoint_path: str = "output/delivery/checkpoints.sqlite"
    checkpoint_backend: str = "mysql"
    model_mode: str = "deepseek"
    max_model_calls: int = 6


def project_values() -> dict:
    values = {}
    for name in (".env", ".env.delivery"):
        values.update(
            {
                k: v
                for k, v in dotenv_values(
                    ROOT / name,
                    encoding="utf-8-sig",
                    interpolate=False,
                ).items()
                if v is not None
            }
        )
    values.update(os.environ)
    return values


@lru_cache
def get_config() -> Config:
    values = project_values()
    key = values.get("DELIVERY_SIGNING_KEY", "")
    if len(key) < 32:
        raise ValueError("Run scripts/setup_delivery.py to configure delivery credentials.")
    url = values.get("DELIVERY_DB_URL")
    if not url:
        url = URL.create(
            "mysql+asyncmy",
            username=values.get("MYSQL_USER", "root"),
            password=values.get("MYSQL_PASSWORD"),
            host=values.get("MYSQL_HOST", "127.0.0.1"),
            port=int(values.get("MYSQL_PORT", 3306)),
            database=DATABASE,
            query={"charset": "utf8mb4"},
        ).render_as_string(hide_password=False)
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite" and parsed.database != DATABASE:
        raise ValueError("Delivery application must use the isolated aftersales_delivery database.")
    accounts = json.loads(values.get("DELIVERY_ACCOUNTS_JSON", "{}"))
    if not accounts:
        raise ValueError("Delivery accounts are not configured.")
    return Config(
        db_url=url,
        signing_key=key,
        accounts=accounts,
        mcp_url=values.get("DELIVERY_MCP_URL", "http://127.0.0.1:8091/mcp"),
        checkpoint_backend=values.get("DELIVERY_CHECKPOINT_BACKEND", "mysql"),
        checkpoint_path=values.get(
            "DELIVERY_CHECKPOINT_PATH", "output/delivery/checkpoints.sqlite"
        ),
        model_mode=values.get("DELIVERY_MODEL_MODE", "deepseek"),
    )
