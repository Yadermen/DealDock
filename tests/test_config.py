import pytest
from pydantic import ValidationError

from steam_radar.bot.admin import is_admin
from steam_radar.config import Settings
from steam_radar.constants import REGIONS


def test_admin_ids_are_parsed() -> None:
    settings = Settings(admin_ids="1, 2,3")
    assert settings.admin_ids == frozenset({1, 2, 3})


def test_unknown_user_is_not_admin() -> None:
    settings = Settings(admin_ids="1,2")
    assert is_admin(1, settings)
    assert not is_admin(999, settings)


def test_core_regions_are_supported() -> None:
    assert REGIONS["RU"].currency == "RUB"
    assert REGIONS["BY"].price_group == "USD_CIS"
    assert REGIONS["KZ"].currency == "KZT"
    assert REGIONS["UA"].currency == "UAH"


def test_default_app_timezone() -> None:
    assert Settings().app_timezone == "Europe/Warsaw"


def test_invalid_runtime_values_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(app_timezone="UTC+3")
    with pytest.raises(ValidationError):
        Settings(monitor_batch_size=0)
    with pytest.raises(ValidationError):
        Settings(health_port=70000)
