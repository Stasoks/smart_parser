from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Smart Parser"
    app_env: Literal["dev", "prod", "test"] = "dev"
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: Path = Path("data")
    database_url: str = "sqlite:///./data/smart_parser.db"

    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.5-flash"
    openrouter_vision_model: str = "google/gemini-2.5-flash"
    openrouter_site_url: str = "http://localhost:8000"
    openrouter_app_name: str = "Smart Parser"
    openrouter_timeout_s: float = 90.0
    ai_enabled: bool = True

    web_search_provider: Literal["ddgs", "tavily", "disabled"] = "ddgs"
    tavily_api_key: str = ""
    web_search_max_results: int = 5

    avito_headless: bool = True
    avito_proxy_urls: str = ""
    avito_min_delay_s: float = 1.8
    avito_max_delay_s: float = 4.2
    avito_timeout_ms: int = 45_000
    avito_max_pages: int = 3
    avito_max_items: int = 80
    avito_max_details: int = 12
    avito_user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
    )
    avito_locale: str = "ru-RU"
    avito_timezone: str = "Europe/Moscow"
    avito_storage_state_path: Path = Path("data/avito_storage_state.json")
    avito_manual_captcha_timeout_s: int = 180
    blocked_screenshot_dir: Path = Path("data/debug")

    deep_analysis_top_n: int = 10
    vision_max_images: int = 4
    llm_batch_size: int = 12
    minimum_comparables: int = 3

    @property
    def avito_proxy_list(self) -> list[str]:
        return [part.strip() for part in self.avito_proxy_urls.split(",") if part.strip()]

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.blocked_screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.avito_storage_state_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
