"""Read local script secrets without loading model settings or changing the environment."""

import os
from getpass import getpass
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def get_mysql_password() -> str:
    """Use configured MYSQL_PASSWORD; prompt only when no value is configured."""
    if "MYSQL_PASSWORD" in os.environ:
        return os.environ["MYSQL_PASSWORD"]
    values = dotenv_values(
        dotenv_path=PROJECT_ROOT / ".env",
        encoding="utf-8-sig",
        interpolate=False,
    )
    password = values.get("MYSQL_PASSWORD")
    if password is not None:
        return password
    return getpass("MySQL password (hidden): ")
