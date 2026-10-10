"""VATSIMControlApp.builder_context: the airport search index follows the data."""

import pytest

from tests.conftest import BUILDER_AIRPORTS
from ui import config as ui_config
from ui.app import VATSIMControlApp


async def test_builder_context_has_no_index_before_the_airport_data_loads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ui_config, "UNIFIED_AIRPORT_DATA", None)
    app = VATSIMControlApp(airport_allowlist=["KSFO"])

    context = app.builder_context()

    assert context.search_index is None
    assert context.airport_idents == frozenset()
    assert context.preset_names == frozenset()
    assert context.tracked == frozenset({"KSFO"})


async def test_builder_context_builds_the_index_once_per_data_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ui_config, "UNIFIED_AIRPORT_DATA", BUILDER_AIRPORTS)
    app = VATSIMControlApp(airport_allowlist=["KSFO"])

    first = app.builder_context()
    again = app.builder_context()
    monkeypatch.setattr(ui_config, "UNIFIED_AIRPORT_DATA", dict(BUILDER_AIRPORTS))
    replaced = app.builder_context()

    assert first.search_index is not None
    assert "KSAC" in first.airport_idents
    assert again.search_index is first.search_index
    assert replaced.search_index is not None
    assert replaced.search_index is not first.search_index
