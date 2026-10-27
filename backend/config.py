from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    model_api_key: str = ""
    meta_base_url: str = "https://api.meta.ai/v1"
    llm_provider: Literal["meta", "assemblyai"] = "meta"
    asr_provider: Literal["meta", "assemblyai"] = "meta"
    assemblyai_api_key: str = ""
    baseten_api_key: str = ""

    # Triage spends Meta credit. Runs need an execution code, are capped per day, and the schedule
    # (off, daily, hourly) is stored in the database and changed from the dashboard.
    execution_code: str = ""
    triage_budget_per_run: int = 2
    triage_batch_limit: int = 5
    triage_daily_cap: int = 30

    firms_map_key: str = ""

    map_provider: Literal["maplibre", "google"] = "maplibre"
    google_maps_key: str = ""

    wa_access_token: str = ""
    wa_phone_number_id: str = ""
    wa_verify_token: str = ""
    wa_app_secret: str = ""
    wa_verifier_numbers: str = ""

    verifier_token: str = ""

    data_dir: Path = REPO_ROOT / "data"
    fixtures_dir: Path = REPO_ROOT / "fixtures"
    frontend_dir: Path = REPO_ROOT / "frontend"
    fixture_mode: bool = True
    scheduler_enabled: bool = False
    firms_poll_minutes: int = 30

    # Set at image build time so a deploy can be proven to run the new code.
    git_sha: str = "dev"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "karhutla.sqlite3"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


@lru_cache
def get_settings() -> Settings:
    return Settings()
