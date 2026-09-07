"""Configuration loaded from environment variables / .env file."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo layout: backend/app/core/config.py -> parents[3] is the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Enterprise AI Customer Service Agent"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    host: str = "127.0.0.1"
    port: int = 8000

    # 本地开发默认指向 docker-compose 中的 PostgreSQL。
    # 测试/迁移可通过环境变量 DATABASE_URL 覆盖,例如 SQLite:
    #   DATABASE_URL=sqlite+pysqlite:///./local.db
    database_url: str = "postgresql+psycopg://acsa:acsa_dev_password@127.0.0.1:5432/acsa"
    database_echo: bool = False

    # ---- LLM (Phase 7B: real DeepSeek integration) ----
    # 默认关闭:没有 DEEPSEEK_API_KEY / 测试 / CI / 演示现场网络异常时,
    # Agent 仍然可以走确定性流程运行(见 docs/DECISIONS.md Decision 040/041)。
    # 打开后 /demo/chat 使用真实 DeepSeek 做意图理解 + 最终回复生成;
    # 任何一次 LLM 失败都会安全回退到确定性流程,绝不绕过 Risk / Approval / Verify。
    llm_enabled: bool = False
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"
    # OpenAI-compatible HTTP 超时(秒),网络异常会统一归类为 LLM 层错误并回退。
    deepseek_timeout_seconds: float = 30.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance (read once per process)."""
    return Settings()
