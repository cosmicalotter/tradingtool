"""Configuración leída de variables de entorno / archivo `.env` (nunca del código).

Todas las variables usan el prefijo ``TT_``. Ver `.env.example`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PriceSourceName = Literal["massive", "tiingo", "eodhd", "alpaca", "ibkr", "csv"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="TT_",
        extra="ignore",
    )

    # --- Rutas ---
    data_dir: Path = Path("data")
    config_dir: Path = Path("config")

    # --- SEC EDGAR ---
    # La SEC exige identificarse: "Nombre Apellido correo@ejemplo.com".
    sec_user_agent: str = ""
    # La SEC permite como máximo 10 solicitudes/segundo; usamos menos por cortesía.
    sec_max_requests_per_second: float = Field(default=5.0, gt=0, le=10)

    # --- IBKR (solo lectura en esta fase) ---
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 4002  # IB Gateway paper. TWS paper = 7497.
    ibkr_client_id: int = 17
    ibkr_account: str = ""
    # Por seguridad solo se aceptan cuentas paper (prefijo "DU"). Ponerlo en true
    # permite conectarse en SOLO LECTURA a una cuenta real (nunca envía órdenes).
    ibkr_allow_live_readonly: bool = False

    # --- Precios ---
    price_source: PriceSourceName = "massive"
    massive_api_key: SecretStr | None = None
    # Historia que cubre tu plan de Massive (el gratuito: ~2 años). Si pagas más, súbelo.
    massive_history_days: int = 730
    tiingo_api_key: SecretStr | None = None
    eodhd_api_key: SecretStr | None = None  # opcional: un mes pagado para historia larga
    alpaca_key_id: SecretStr | None = None  # gratis: historia desde 2016
    alpaca_secret_key: SecretStr | None = None
    benchmark_ticker: str = "SPY"
    # Secundario: las compras de insiders se concentran en empresas pequeñas; IWM controla
    # (en parte) que el exceso no sea solo "prima de tamaño".
    benchmark_secondary_ticker: str = "IWM"

    @field_validator("sec_user_agent")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tradingtool.duckdb"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.raw_dir, self.log_dir):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
