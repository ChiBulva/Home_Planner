from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("HOUSEBOARD_APP_NAME", "HouseBoard")
    secret_key: str = os.getenv("HOUSEBOARD_SECRET_KEY", "change-me-before-exposing")
    database_url: str = os.getenv(
        "HOUSEBOARD_DATABASE_URL", f"sqlite:///{BASE_DIR / 'houseboard.db'}"
    )
    session_max_age: int = int(os.getenv("HOUSEBOARD_SESSION_MAX_AGE", "1209600"))
    reset_hour: int = int(os.getenv("HOUSEBOARD_RESET_HOUR", "4"))
    reset_minute: int = int(os.getenv("HOUSEBOARD_RESET_MINUTE", "0"))
    totp_issuer: str = os.getenv("HOUSEBOARD_TOTP_ISSUER", "HouseBoard")


settings = Settings()
