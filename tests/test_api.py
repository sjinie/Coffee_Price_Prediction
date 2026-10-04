"""DB 없이 확인하는 API 입력 계약."""
from coffee import api
from coffee.config import SETTINGS


def test_api_weather_regions_match_configured_sources():
    assert list(api.REGIONS) == [region["id"] for region in SETTINGS["weather"]["regions"]]
