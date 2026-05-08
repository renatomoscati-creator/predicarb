import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv


EnvName = Literal["demo", "prod"]


@dataclass
class Settings:
    env: EnvName
    api_base_url: str        # Polymarket CLOB API (orderbook, orders, balance)
    gamma_api_base_url: str  # Polymarket Gamma API (market listings)
    log_level: str
    data_dir: Path
    logs_dir: Path

    # Polymarket L2 authentication fields.
    polymarket_api_key: Optional[str] = None
    polymarket_api_secret: Optional[str] = None
    polymarket_api_passphrase: Optional[str] = None
    polymarket_address: Optional[str] = None   # wallet address (0x...)


def _load_env_file(env: EnvName) -> None:
    """
    Load environment variables from `.env.demo` or `.env.prod` if present.
    Existing environment variables take precedence.
    """
    env_file = Path(f".env.{env}")
    if env_file.exists():
        load_dotenv(env_file, override=False)


def get_settings() -> Settings:
    env_str = os.getenv("POLYMARKET_ENV", "demo").lower()
    env: EnvName = "demo" if env_str != "prod" else "prod"

    _load_env_file(env)

    api_base_url = os.getenv("POLYMARKET_API_BASE_URL", "https://clob.polymarket.com")
    gamma_api_base_url = os.getenv("POLYMARKET_GAMMA_API_BASE_URL", "https://gamma-api.polymarket.com")
    log_level = os.getenv("POLYMARKET_LOG_LEVEL", "INFO")

    project_root = Path(__file__).resolve().parent.parent
    data_dir = project_root / "data"
    logs_dir = project_root / "logs"

    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    polymarket_api_key = os.getenv("POLYMARKET_API_KEY") or None
    polymarket_api_secret = os.getenv("POLYMARKET_API_SECRET") or None
    polymarket_api_passphrase = os.getenv("POLYMARKET_API_PASSPHRASE") or None
    polymarket_address = os.getenv("POLYMARKET_ADDRESS") or None

    return Settings(
        env=env,
        api_base_url=api_base_url,
        gamma_api_base_url=gamma_api_base_url,
        log_level=log_level,
        data_dir=data_dir,
        logs_dir=logs_dir,
        polymarket_api_key=polymarket_api_key,
        polymarket_api_secret=polymarket_api_secret,
        polymarket_api_passphrase=polymarket_api_passphrase,
        polymarket_address=polymarket_address,
    )
