"""Application configuration using pydantic-settings."""

from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- LLM Provider ---
    azure_openai_endpoint: Optional[str] = Field(default=None)
    azure_openai_api_key: Optional[str] = Field(default=None)
    azure_openai_api_version: str = Field(default="2024-02-15-preview")
    azure_openai_deployment_name: Optional[str] = Field(default=None)
    azure_openai_embedding_deployment: Optional[str] = Field(default=None)

    openai_api_key: Optional[str] = Field(default=None)
    openai_base_url: Optional[str] = Field(default=None)
    openai_model: str = Field(default="gpt-4o-mini")
    openai_embedding_model: str = Field(default="text-embedding-3-small")

    # --- Vector Store ---
    qdrant_host: str = Field(default="localhost")
    qdrant_port: int = Field(default=6333)
    qdrant_api_key: Optional[str] = Field(default=None)
    qdrant_collection: str = Field(default="enterprise_kb")

    azure_ai_search_endpoint: Optional[str] = Field(default=None)
    azure_ai_search_api_key: Optional[str] = Field(default=None)
    azure_ai_search_index_name: Optional[str] = Field(default=None)
    azure_ai_search_api_version: str = Field(default="2024-07-01")

    # Managed identity (optional): when true, tokens from DefaultAzureCredential
    # are used instead of keys for Azure OpenAI and Azure AI Search. The
    # `azure-identity` package is optional — without it the app falls back to
    # keys or mock/offline mode, never crashing.
    azure_use_managed_identity: bool = Field(default=False)

    # --- Memory Layer ---
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379)
    redis_db: int = Field(default=0)
    redis_password: Optional[str] = Field(default=None)
    memory_ttl_seconds: int = Field(default=86400)

    # --- Application ---
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8000)
    app_debug: bool = Field(default=True)
    log_level: str = Field(default="INFO")

    # --- Security ---
    secret_key: str = Field(default="change-this-in-production")
    allowed_origins: str = Field(default="http://localhost:3000")

    # --- Execution Agent ---
    allowed_execution_domains: str = Field(default="")
    execution_require_approval: bool = Field(default=True)

    # --- Review Agent (Phase 3) ---
    # Deterministic checks always run; the LLM groundedness pass adds one
    # extra model call (~20-30s on CPU) per non-trivial answer.
    review_enabled: bool = Field(default=True)
    review_llm_enabled: bool = Field(default=True)

    # --- Database ---
    # Free default: local SQLite file. Set a PostgreSQL URL to switch (needs
    # requirements-postgres.txt). SQLite stays the fallback if psycopg is absent.
    database_url: str = Field(default="")

    # --- Tool framework (Phase 4) ---
    tools_enabled: bool = Field(default=True)
    sqlite_path: str = Field(default="data/enterprise.db")
    reports_dir: str = Field(default="reports")

    # --- ITSM (Phase 7) ---
    # SLA deadlines in hours per suggested priority, e.g. "critical:2,high:8,medium:24,low:72"
    itsm_sla_hours: str = Field(
        default="critical:2,high:8,medium:24,low:72"
    )

    # --- Security (Phase 5) ---
    # Auth is OFF by default so the app works out of the box. When enabled,
    # API_KEYS maps keys to roles: "key1:admin,key2:user,key3:viewer".
    auth_enabled: bool = Field(default=False)
    api_keys: str = Field(default="")

    # --- Monitoring (Phase 5) ---
    audit_dir: str = Field(default="logs")
    tracing_enabled: bool = Field(default=True)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }

    @staticmethod
    def _is_placeholder(value: Optional[str]) -> bool:
        """Detect placeholder values from .env.example files."""
        if not value:
            return True
        lowered = value.lower()
        placeholders = (
            "your-resource",
            "your-service",
            "your-key",
            "your-",
            "example.com",
            "change-this",
            "sk-your",
        )
        return any(marker in lowered for marker in placeholders)

    @property
    def use_azure_openai(self) -> bool:
        endpoint_ok = bool(
            self.azure_openai_endpoint
            and not self._is_placeholder(self.azure_openai_endpoint)
        )
        if not endpoint_ok:
            return False
        # Managed identity replaces the need for an API key.
        if self.azure_use_managed_identity:
            return True
        return bool(self.azure_openai_api_key and not self._is_placeholder(self.azure_openai_api_key))

    @property
    def use_openai(self) -> bool:
        """True when an OpenAI-compatible endpoint (OpenAI or Ollama) is configured."""
        return bool(
            self.openai_api_key
            and not self._is_placeholder(self.openai_api_key)
        )

    @property
    def use_qdrant(self) -> bool:
        return bool(self.qdrant_host and not self._is_placeholder(self.qdrant_host))

    @property
    def use_azure_ai_search(self) -> bool:
        endpoint_ok = bool(
            self.azure_ai_search_endpoint
            and not self._is_placeholder(self.azure_ai_search_endpoint)
        )
        if not endpoint_ok:
            return False
        if self.azure_use_managed_identity:
            return True
        return bool(self.azure_ai_search_api_key and not self._is_placeholder(self.azure_ai_search_api_key))

    @property
    def allowed_origin_list(self) -> List[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def allowed_execution_domain_list(self) -> List[str]:
        return [d.strip() for d in self.allowed_execution_domains.split(",") if d.strip()]


@lru_cache()
def get_settings() -> Settings:
    return Settings()