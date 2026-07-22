"""Runtime configuration loaded from a protected file outside the repository."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ENV_FILE = Path("~/.config/wordsbot/env").expanduser()
MODEL_PRICES_USD_PER_MILLION = {
    "gpt-5.6-terra": (2.5, 15.0),
    "gpt-5.6-luna": (1.0, 6.0),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    telegram_bot_token: SecretStr = Field(alias="TELEGRAM_BOT_TOKEN")
    database_url: str = Field(alias="DATABASE_URL")
    owner_chat_id: int = Field(alias="OWNER_CHAT_ID")
    spouse_chat_id: int | None = Field(default=None, alias="SPOUSE_CHAT_ID")
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    tutor_model: str = Field(default="gpt-5.4-mini", alias="TUTOR_MODEL")
    curator_model: str = Field(default="gpt-5.4-mini", alias="CURATOR_MODEL")
    grader_model: str | None = Field(default=None, alias="GRADER_MODEL")
    llm_reservation_usd: float = Field(default=0.25, alias="LLM_RESERVATION_USD", gt=0)
    llm_input_usd_per_million: float = Field(
        default=0.75, alias="LLM_INPUT_USD_PER_MILLION", ge=0
    )
    llm_output_usd_per_million: float = Field(
        default=4.5, alias="LLM_OUTPUT_USD_PER_MILLION", ge=0
    )
    tutor_input_usd_per_million: float | None = Field(
        default=None, alias="TUTOR_INPUT_USD_PER_MILLION", ge=0
    )
    tutor_output_usd_per_million: float | None = Field(
        default=None, alias="TUTOR_OUTPUT_USD_PER_MILLION", ge=0
    )
    curator_input_usd_per_million: float | None = Field(
        default=None, alias="CURATOR_INPUT_USD_PER_MILLION", ge=0
    )
    curator_output_usd_per_million: float | None = Field(
        default=None, alias="CURATOR_OUTPUT_USD_PER_MILLION", ge=0
    )
    grader_input_usd_per_million: float | None = Field(
        default=None, alias="GRADER_INPUT_USD_PER_MILLION", ge=0
    )
    grader_output_usd_per_million: float | None = Field(
        default=None, alias="GRADER_OUTPUT_USD_PER_MILLION", ge=0
    )
    log_path: Path = Field(default=Path("~/Library/Logs/wordsbot/bot.log"), alias="BOT_LOG_PATH")
    push_check_seconds: int = Field(default=60, alias="PUSH_CHECK_SECONDS", ge=30)

    @property
    def allowed_chat_ids(self) -> frozenset[int]:
        values = {self.owner_chat_id}
        if self.spouse_chat_id is not None:
            values.add(self.spouse_chat_id)
        return frozenset(values)

    def prices_for(self, role: str) -> tuple[float, float]:
        if role not in {"tutor", "curator", "grader"}:
            raise ValueError("unsupported LLM role")
        input_value = getattr(self, f"{role}_input_usd_per_million")
        output_value = getattr(self, f"{role}_output_usd_per_million")
        model = (
            self.curator_model
            if role == "curator"
            else (self.grader_model or self.tutor_model)
            if role == "grader"
            else self.tutor_model
        )
        model_input, model_output = MODEL_PRICES_USD_PER_MILLION.get(
            model,
            (self.llm_input_usd_per_million, self.llm_output_usd_per_million),
        )
        return (
            model_input if input_value is None else input_value,
            model_output if output_value is None else output_value,
        )


def load_settings() -> Settings:
    path = Path(os.environ.get("WORDSBOT_ENV_FILE", DEFAULT_ENV_FILE)).expanduser()
    load_dotenv(path, override=False)
    return Settings()  # type: ignore[call-arg]
