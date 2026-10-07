"""Fixtures compartidas. Ningún test usa red: todo con datos locales o falsos."""

from __future__ import annotations

from pathlib import Path

import pytest

from tradingtool.config import AppConfig
from tradingtool.db import connect
from tradingtool.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def con():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def app_config() -> AppConfig:
    return AppConfig()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        config_dir=Path(__file__).parents[1] / "config",
        sec_user_agent="Test User test@example.com",
    )
