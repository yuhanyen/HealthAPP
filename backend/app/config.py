"""應用設定：全部來自環境變數／專案根目錄的 .env。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(os.environ.get("HEALTHAPP_ROOT", Path(__file__).resolve().parents[2]))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: Literal["dev", "test", "prod"] = "dev"

    pg_host: str = "127.0.0.1"
    pg_port: int = 55432
    pg_db: str = "healthapp"
    pg_superuser: str = "postgres"
    pg_superpass: str = ""

    idn_app_password: str = ""
    idn_user_scoped_password: str = ""
    idn_site_scoped_password: str = ""
    anon_reader_password: str = ""
    anon_writer_password: str = ""
    audit_writer_password: str = ""

    redis_url: str = "redis://127.0.0.1:56379/0"
    event_bus: Literal["redis", "memory"] = "redis"
    event_stream: str = "events"

    jwt_secret: str = ""
    jwt_ttl_minutes: int = 480
    bcrypt_rounds: int = 12

    field_encryption_key: str = ""
    national_id_pepper: str = ""

    # SPEC §13 預設決策
    k_default: int = 5
    confirm_ttl_hours: int = 24
    a1_parallelism: int = 8  # SPEC §11：每日批次平行度可配置
    rate_login_per_min: int = 10  # SPEC §11 API 速率限制：同一帳號＋來源 IP
    rate_chat_per_min: int = 20  # Agent 對話：同一帳號
    deletion_sla_hours: int = 24
    timezone: str = "Asia/Taipei"

    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"

    # 示範區（一期單一區）
    district: str = "北區"

    # 外部開放資料／API（無金鑰則自動 Mock）
    cwa_api_key: str = ""
    moenv_api_key: str = ""
    env_cwa_station_id: str = ""
    env_moenv_site: str = ""
    google_places_api_key: str = ""
    places_villages: str = ""  # JSON：{"place_id": "N01", ...}
    places_merchants: str = ""  # JSON：{"M01": "place_id", ...}（商家評分，SPEC §17.2）
    event_feeds: str = ""  # JSON 陣列：公開活動公告的 RSS／Atom 網址（須在來源白名單內）

    @property
    def i18n_dir(self) -> Path:
        return PROJECT_ROOT / "i18n"

    @property
    def skills_dir(self) -> Path:
        return PROJECT_ROOT / "skills"

    @property
    def reports_dir(self) -> Path:
        return PROJECT_ROOT / "backend" / "reports"


@lru_cache
def get_settings() -> Settings:
    return Settings()
