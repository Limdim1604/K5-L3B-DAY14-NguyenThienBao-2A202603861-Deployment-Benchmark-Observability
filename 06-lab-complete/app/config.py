"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường.

Biến môi trường (liệt kê đủ trong .env.example):
    PORT, ENVIRONMENT, AGENT_API_KEY, REDIS_URL,
    RATE_LIMIT_PER_MINUTE, DAILY_BUDGET_USD, MONTHLY_BUDGET_USD, LOG_LEVEL

Không có secret nào nằm trong code: AGENT_API_KEY mặc định là chuỗi rỗng,
khi rỗng thì mọi request /ask đều bị từ chối (fail closed).
"""
import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass
class Settings:
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    agent_api_key: str = field(default_factory=lambda: os.getenv("AGENT_API_KEY", ""))
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))

    rate_limit_per_minute: int = field(default_factory=lambda: _env_int("RATE_LIMIT_PER_MINUTE", 10))
    daily_budget_usd: float = field(default_factory=lambda: _env_float("DAILY_BUDGET_USD", 1.0))
    monthly_budget_usd: float = field(default_factory=lambda: _env_float("MONTHLY_BUDGET_USD", 10.0))

    def validate(self) -> "Settings":
        """Fail fast: production mà thiếu API key thì không cho khởi động."""
        if self.environment == "production" and not self.agent_api_key:
            raise ValueError("AGENT_API_KEY must be set when ENVIRONMENT=production")
        if self.rate_limit_per_minute <= 0:
            raise ValueError("RATE_LIMIT_PER_MINUTE must be > 0")
        return self


settings = Settings().validate()
