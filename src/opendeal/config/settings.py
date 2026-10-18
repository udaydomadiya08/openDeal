"""Configuration management for OpenDeal."""

import re
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SECConfig(BaseModel):
    """Configuration for SEC EDGAR official API client."""
    user_agent: str = Field(
        default="OpenDealPlatform research@opendeal-intelligence.org",
        description="User-Agent header required by SEC EDGAR (Format: Organization ContactEmail)",
    )
    base_url: str = Field(
        default="https://data.sec.gov",
        description="Base URL for SEC EDGAR data endpoints",
    )
    rate_limit_per_second: float = Field(
        default=5.0,
        ge=0.1,
        le=10.0,
        description="Max requests per second (SEC hard maximum is 10 req/sec)",
    )
    timeout_seconds: float = Field(
        default=20.0,
        ge=1.0,
        le=120.0,
        description="HTTP request timeout in seconds",
    )
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum retry attempts on transient network/429 errors",
    )

    @field_validator("user_agent")
    @classmethod
    def validate_sec_user_agent(cls, v: str) -> str:
        v = v.strip()
        if not v or "@" not in v:
            raise ValueError(
                "SEC requires a descriptive User-Agent including an email address. "
                "Format: 'AppName/Version AdminContact@domain.com'"
            )
        return v


class StorageConfig(BaseModel):
    """Configuration for data persistence layer."""
    database_path: Optional[str] = Field(
        default=None,
        description="Path to SQLite database file, ':memory:' for transient storage, or None for canonical per-user OS data directory",
    )
    echo_sql: bool = Field(
        default=False,
        description="Whether to log executed SQL statements",
    )

    @property
    def effective_database_path(self) -> str:
        """Resolve effective database path following system precedence policy."""
        from opendeal.config.paths import resolve_database_path
        return resolve_database_path(self.database_path)


class CompaniesHouseConfig(BaseModel):
    """Configuration for official UK Companies House API client."""
    api_key: str = Field(
        default="",
        description="Companies House REST API key (HTTP Basic username with empty password)",
    )
    base_url: str = Field(
        default="https://api.company-information.service.gov.uk",
        description="Base URL for official UK Companies House API endpoints",
    )
    rate_limit_per_second: float = Field(
        default=2.0,
        ge=0.1,
        le=1000.0,
        description="Max requests per second (Companies House allows 600 req per 5 minutes = 2.0 req/sec)",
    )
    timeout_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=60.0,
        description="HTTP request timeout in seconds",
    )
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum retry attempts on transient network or 429 rate limit errors",
    )
    attribution_notice: str = Field(
        default="Crown copyright, licensed under the Open Government Licence v3.0",
        description="Statutory attribution required under OGL v3.0",
    )


class Settings(BaseSettings):
    """Application-wide settings loaded from environment or defaults."""
    model_config = SettingsConfigDict(
        env_prefix="OPENDEAL_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "OpenDeal"
    environment: str = "development"
    debug: bool = False
    data_dir: Path = Field(default_factory=lambda: Path("./data"))
    sec: SECConfig = Field(default_factory=SECConfig)
    companies_house: CompaniesHouseConfig = Field(default_factory=CompaniesHouseConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)


# Global default settings instance
settings = Settings()
