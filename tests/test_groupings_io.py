"""Strict favorites.json I/O: the read, save, delete and rename backend functions.

No Textual: these tests drive `backend.core.groupings` directly, with the user
data directory pointed at a temp directory as `tests/conftest.py` does.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

import common.paths
from backend.cache import manager as cache_manager
from backend.core.groupings import (
    GroupingError,
    GroupingExistsError,
    GroupingNameError,
    GroupingNotFoundError,
    UserGrouping,
    airport_idents,
    delete_user_grouping,
    get_user_grouping,
    preset_grouping_names,
    rename_user_grouping,
    save_user_grouping,
)

PRESET_NAMES = {"ZOA All", "Bay Area"}
AIRPORT_IDENTS = {"KSFO", "KOAK", "SFO", "OAK"}

UNIFIED: dict[str, dict[str, Any]] = {
    "KSFO": {"icao": "KSFO", "faa": "SFO", "iata": "SFO", "artcc": "ZOA"},
    "KJFK": {"icao": "KJFK", "faa": "JFK", "iata": "JFK", "artcc": "ZNY"},
    "0Q5": {"icao": "", "faa": "0Q5", "iata": "", "artcc": "ZOA"},
}


@pytest.fixture(autouse=True)
def fresh_artcc_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the process-wide ARTCC groupings cache another test file may fill."""
    monkeypatch.setattr(cache_manager, "_ARTCC_GROUPINGS", None)


@pytest.fixture
def favorites_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the user data directory at a temp dir; return the favorites path."""
    user_data_dir = tmp_path / "VATSIMControlRecs"
    user_data_dir.mkdir()
    monkeypatch.setattr(common.paths, "get_user_data_dir", lambda: user_data_dir)
    return user_data_dir / "favorites.json"


def write_favorites(path: Path, content: Any) -> None:
    """Write a favorites.json body, verbatim when it is a string."""
    text = content if isinstance(content, str) else json.dumps(content, indent=2)
    path.write_text(text, encoding="utf-8")


def read_favorites(path: Path) -> Any:
    """Read a favorites.json body back."""
    return json.loads(path.read_text(encoding="utf-8"))


def save(
    name: str,
    items: list[str],
    filters: dict[str, str] | None = None,
    *,
    overwrite: bool = False,
) -> None:
    """Call `save_user_grouping` with the fixture name and ident sets."""
    save_user_grouping(
        UserGrouping(name=name, items=tuple(items), filters=dict(filters or {})),
        overwrite=overwrite,
        preset_names=PRESET_NAMES,
        airport_idents=AIRPORT_IDENTS,
    )


def rename(old: str, new: UserGrouping, *, overwrite: bool = False) -> None:
    """Call `rename_user_grouping` with the fixture name and ident sets."""
    rename_user_grouping(
        old,
        new,
        overwrite=overwrite,
        preset_names=PRESET_NAMES,
        airport_idents=AIRPORT_IDENTS,
    )


def test_get_reads_the_bare_list_shape(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO", "KOAK"]})

    assert get_user_grouping("Alpha") == UserGrouping("Alpha", ("KSFO", "KOAK"), {})


def test_get_reads_the_dict_shape(favorites_file: Path) -> None:
    write_favorites(
        favorites_file,
        {"Alpha": {"airports": ["KSFO"], "filters": {"KSFO": "arr"}}},
    )

    assert get_user_grouping("Alpha") == UserGrouping(
        "Alpha", ("KSFO",), {"KSFO": "arr"}
    )


@pytest.mark.parametrize(
    "entry",
    [["KSFO", 7], {"airports": ["KSFO", 7]}],
    ids=["bare list", "dict shape"],
)
def test_a_member_that_is_not_a_string_is_corrupt(
    favorites_file: Path, entry: Any
) -> None:
    write_favorites(favorites_file, {"Alpha": entry})

    with pytest.raises(GroupingError) as excinfo:
        get_user_grouping("Alpha")

    message = str(excinfo.value)
    assert str(favorites_file) in message
    assert "Alpha" in message


def test_get_returns_none_for_a_name_that_is_not_there(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"]})

    assert get_user_grouping("Beta") is None


def test_get_returns_none_when_the_file_is_missing(favorites_file: Path) -> None:
    assert get_user_grouping("Alpha") is None


def test_save_writes_the_bare_list_shape_with_no_filters(favorites_file: Path) -> None:
    save("Alpha", ["KSFO", "KOAK"])

    assert read_favorites(favorites_file) == {"Alpha": ["KSFO", "KOAK"]}


def test_save_writes_lf_line_endings(favorites_file: Path) -> None:
    save("Alpha", ["KSFO"])

    assert b"\r\n" not in favorites_file.read_bytes()


def test_save_round_trips_filters_in_the_dict_shape(favorites_file: Path) -> None:
    save("Alpha", ["KSFO"], {"KSFO": "arr"})

    assert read_favorites(favorites_file) == {
        "Alpha": {"airports": ["KSFO"], "filters": {"KSFO": "arr"}}
    }
    assert get_user_grouping("Alpha") == UserGrouping(
        "Alpha", ("KSFO",), {"KSFO": "arr"}
    )


def test_save_keeps_the_given_order_and_drops_duplicates(favorites_file: Path) -> None:
    save("Alpha", ["KOAK", "KSFO", "KOAK", "KSJC"])

    assert read_favorites(favorites_file)["Alpha"] == ["KOAK", "KSFO", "KSJC"]


def test_save_drops_filters_that_are_not_dep_or_arr(favorites_file: Path) -> None:
    save("Alpha", ["KSFO"], {"KSFO": "both", "KOAK": "arr", "KSJC": "sideways"})

    assert read_favorites(favorites_file)["Alpha"] == {
        "airports": ["KSFO"],
        "filters": {"KOAK": "arr"},
    }


def test_save_keeps_a_filter_keyed_by_a_nested_member(favorites_file: Path) -> None:
    save("Alpha", ["Bay Area"], {"KOAK": "arr"})

    assert read_favorites(favorites_file)["Alpha"] == {
        "airports": ["Bay Area"],
        "filters": {"KOAK": "arr"},
    }


def test_save_appends_a_new_entry_after_the_existing_ones(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"Zulu": ["KSFO"], "Alpha": ["KOAK"]})

    save("New", ["KSJC"])

    assert list(read_favorites(favorites_file)) == ["Zulu", "Alpha", "New"]


def test_save_stores_the_stripped_name(favorites_file: Path) -> None:
    save("  Alpha  ", ["KSFO"])

    assert read_favorites(favorites_file) == {"Alpha": ["KSFO"]}


def test_save_refuses_an_existing_name_without_overwrite(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"]})

    with pytest.raises(GroupingExistsError, match="already one of my groupings"):
        save("Alpha", ["KOAK"])

    assert read_favorites(favorites_file) == {"Alpha": ["KSFO"]}


def test_save_refuses_a_stored_name_in_another_case(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"Bay Trip": ["KOAK"]})

    with pytest.raises(GroupingExistsError, match="already one of my groupings"):
        save("bay trip", ["KSJC"])

    assert read_favorites(favorites_file) == {"Bay Trip": ["KOAK"]}


def test_save_replaces_a_stored_name_in_another_case_under_the_new_spelling(
    favorites_file: Path,
) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"], "Bay Trip": ["KOAK"]})

    save("bay trip", ["KSJC"], overwrite=True)

    data = read_favorites(favorites_file)
    assert list(data) == ["Alpha", "bay trip"]
    assert data["bay trip"] == ["KSJC"]


def test_save_replaces_an_existing_name_in_place_with_overwrite(
    favorites_file: Path,
) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"], "Beta": ["KOAK"]})

    save("Alpha", ["KSJC"], overwrite=True)

    data = read_favorites(favorites_file)
    assert list(data) == ["Alpha", "Beta"]
    assert data["Alpha"] == ["KSJC"]


def test_save_refuses_a_blank_name(favorites_file: Path) -> None:
    with pytest.raises(GroupingNameError, match="a name is required"):
        save("   ", ["KSFO"])

    assert not favorites_file.exists()


def test_save_refuses_a_preset_name_in_another_case(favorites_file: Path) -> None:
    with pytest.raises(GroupingNameError, match="that name belongs to a preset"):
        save("zoa all", ["KSFO"])


def test_save_refuses_an_airport_ident(favorites_file: Path) -> None:
    with pytest.raises(GroupingNameError, match="that name is an airport ident"):
        save("ksfo", ["KOAK"])


def test_save_allows_an_existing_favorite_that_shadows_a_preset(
    favorites_file: Path,
) -> None:
    write_favorites(favorites_file, {"ZOA All": ["KSFO"]})

    save("ZOA All", ["KOAK", "KSJC"], overwrite=True)

    assert read_favorites(favorites_file)["ZOA All"] == ["KOAK", "KSJC"]


def test_save_allows_a_shadowing_favorite_saved_under_another_case(
    favorites_file: Path,
) -> None:
    write_favorites(favorites_file, {"ZOA all": ["KSFO"]})

    save("ZOA All", ["KOAK"], overwrite=True)

    data = read_favorites(favorites_file)
    assert list(data) == ["ZOA All"]
    assert data["ZOA All"] == ["KOAK"]


def test_save_preserves_unknown_top_level_entries(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"], "Weird": "text", "Num": 7})

    save("Beta", ["KOAK"])

    data = read_favorites(favorites_file)
    assert data["Weird"] == "text"
    assert data["Num"] == 7
    assert data["Alpha"] == ["KSFO"]
    assert data["Beta"] == ["KOAK"]


def test_save_creates_the_user_data_directory_when_it_is_missing(
    favorites_file: Path,
) -> None:
    parent = favorites_file.parent
    for child in parent.iterdir():
        child.unlink()
    parent.rmdir()

    save("Alpha", ["KSFO"])

    assert read_favorites(favorites_file) == {"Alpha": ["KSFO"]}


def test_delete_of_a_missing_name_raises(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"]})

    with pytest.raises(GroupingNotFoundError, match="not one of my groupings"):
        delete_user_grouping("Beta")

    assert read_favorites(favorites_file) == {"Alpha": ["KSFO"]}


def test_delete_of_a_missing_file_raises(favorites_file: Path) -> None:
    with pytest.raises(GroupingNotFoundError):
        delete_user_grouping("Alpha")


def test_delete_leaves_the_other_entries_byte_for_byte(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"], "Beta": ["KOAK"]})

    delete_user_grouping("Beta")

    expected = json.dumps({"Alpha": ["KSFO"]}, indent=2, ensure_ascii=False)
    assert favorites_file.read_bytes() == expected.encode("utf-8")


def test_rename_onto_an_existing_name_raises_without_overwrite(
    favorites_file: Path,
) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"], "Beta": ["KOAK"]})

    with pytest.raises(GroupingExistsError, match="already one of my groupings"):
        rename("Alpha", UserGrouping("Beta", ("KSJC",), {}))

    assert read_favorites(favorites_file) == {"Alpha": ["KSFO"], "Beta": ["KOAK"]}


def test_rename_onto_an_existing_name_replaces_it_with_overwrite(
    favorites_file: Path,
) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"], "Beta": ["KOAK"]})

    rename("Alpha", UserGrouping("Beta", ("KSJC",), {}), overwrite=True)

    assert read_favorites(favorites_file) == {"Beta": ["KSJC"]}


def test_rename_keeps_the_new_filters_and_removes_the_old_name(
    favorites_file: Path,
) -> None:
    write_favorites(
        favorites_file,
        {"Alpha": {"airports": ["KSFO"], "filters": {"KSFO": "arr"}}},
    )

    rename("Alpha", UserGrouping("Gamma", ("KSJC",), {"KSJC": "dep"}))

    assert read_favorites(favorites_file) == {
        "Gamma": {"airports": ["KSJC"], "filters": {"KSJC": "dep"}}
    }
    assert get_user_grouping("Alpha") is None


def test_rename_keeps_the_entry_position(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"], "Beta": ["KOAK"]})

    rename("Alpha", UserGrouping("Gamma", ("KSJC",), {}))

    data = read_favorites(favorites_file)
    assert list(data) == ["Gamma", "Beta"]
    assert data["Gamma"] == ["KSJC"]


def test_a_case_only_rename_keeps_the_entry_in_place(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"], "Beta": ["KOAK"]})

    rename("Alpha", UserGrouping("alpha", ("KSJC",), {}))

    data = read_favorites(favorites_file)
    assert list(data) == ["alpha", "Beta"]
    assert data["alpha"] == ["KSJC"]


def test_rename_to_the_same_name_updates_the_contents(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"], "Beta": ["KOAK"]})

    rename("Alpha", UserGrouping("Alpha", ("KSJC",), {}))

    data = read_favorites(favorites_file)
    assert list(data) == ["Alpha", "Beta"]
    assert data["Alpha"] == ["KSJC"]


def test_rename_refuses_a_blank_name(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"]})

    with pytest.raises(GroupingNameError, match="a name is required"):
        rename("Alpha", UserGrouping("   ", ("KOAK",), {}))

    assert read_favorites(favorites_file) == {"Alpha": ["KSFO"]}


def test_rename_refuses_a_preset_name_in_another_case(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"]})

    with pytest.raises(GroupingNameError, match="that name belongs to a preset"):
        rename("Alpha", UserGrouping("zoa all", ("KOAK",), {}))

    assert read_favorites(favorites_file) == {"Alpha": ["KSFO"]}


def test_rename_refuses_an_airport_ident(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"]})

    with pytest.raises(GroupingNameError, match="that name is an airport ident"):
        rename("Alpha", UserGrouping("ksfo", ("KOAK",), {}))

    assert read_favorites(favorites_file) == {"Alpha": ["KSFO"]}


def test_rename_of_a_missing_name_raises(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"]})

    with pytest.raises(GroupingNotFoundError, match="not one of my groupings"):
        rename("Nope", UserGrouping("Gamma", ("KSJC",), {}))

    assert read_favorites(favorites_file) == {"Alpha": ["KSFO"]}


def test_a_corrupt_file_makes_every_write_raise_and_leaves_the_bytes(
    favorites_file: Path,
) -> None:
    write_favorites(favorites_file, "{not json")
    before = favorites_file.read_bytes()

    with pytest.raises(GroupingError):
        save("Alpha", ["KSFO"])
    with pytest.raises(GroupingError):
        delete_user_grouping("Alpha")
    with pytest.raises(GroupingError):
        rename("Alpha", UserGrouping("Beta", ("KSFO",), {}))

    assert favorites_file.read_bytes() == before


def test_a_latin1_byte_makes_every_write_raise_and_leaves_the_bytes(
    favorites_file: Path,
) -> None:
    favorites_file.write_bytes('{"Alpha": ["Küst"]}'.encode("latin-1"))
    before = favorites_file.read_bytes()

    with pytest.raises(GroupingError):
        save("Beta", ["KSFO"])
    with pytest.raises(GroupingError):
        delete_user_grouping("Alpha")

    assert favorites_file.read_bytes() == before


def test_a_non_object_top_level_names_the_file(favorites_file: Path) -> None:
    write_favorites(favorites_file, "[1, 2]")

    with pytest.raises(GroupingError) as excinfo:
        save("Alpha", ["KSFO"])

    assert str(favorites_file) in str(excinfo.value)
    assert "not a JSON object" in str(excinfo.value)


def test_a_malformed_entry_names_the_file_and_the_entry(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"Alpha": "text"})

    with pytest.raises(GroupingError) as excinfo:
        get_user_grouping("Alpha")

    message = str(excinfo.value)
    assert str(favorites_file) in message
    assert "Alpha" in message


def test_a_failed_replace_leaves_the_bytes_and_no_stray_temp_file(
    favorites_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"]})
    before = favorites_file.read_bytes()

    def boom(source: str, target: str) -> None:
        raise OSError("disk on fire")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(GroupingError, match="disk on fire"):
        save("Beta", ["KOAK"])

    assert favorites_file.read_bytes() == before
    assert list(favorites_file.parent.iterdir()) == [favorites_file]


def test_a_failed_json_dump_leaves_the_bytes_and_no_stray_temp_file(
    favorites_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_favorites(favorites_file, {"Alpha": ["KSFO"]})
    before = favorites_file.read_bytes()

    def boom(*args: Any, **kwargs: Any) -> None:
        raise ValueError("no room")

    monkeypatch.setattr(json, "dump", boom)

    with pytest.raises(GroupingError, match="no room"):
        save("Beta", ["KOAK"])

    assert favorites_file.read_bytes() == before
    assert list(favorites_file.parent.iterdir()) == [favorites_file]


def test_the_temp_file_is_written_beside_favorites_json(
    favorites_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_named_temporary_file = tempfile.NamedTemporaryFile
    calls: list[dict[str, Any]] = []

    def recording(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return real_named_temporary_file(*args, **kwargs)

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", recording)

    save("Alpha", ["KSFO"])

    assert len(calls) == 1
    assert calls[0]["dir"] == favorites_file.parent
    assert calls[0]["newline"] == "\n"


def test_airport_idents_collects_keys_and_non_empty_fields() -> None:
    assert airport_idents(UNIFIED) == {"KSFO", "SFO", "KJFK", "JFK", "0Q5"}


def test_preset_grouping_names_hold_the_artcc_groupings() -> None:
    names = preset_grouping_names(UNIFIED)

    assert "ZOA All" in names
    assert "ZNY All" in names


def test_a_favorite_shadowing_a_preset_does_not_remove_it(favorites_file: Path) -> None:
    write_favorites(favorites_file, {"ZOA All": ["KSFO"]})

    assert "ZOA All" in preset_grouping_names(UNIFIED)


def test_grouping_error_subclasses_are_distinct() -> None:
    assert issubclass(GroupingNotFoundError, GroupingError)
    assert issubclass(GroupingExistsError, GroupingError)
    assert issubclass(GroupingNameError, GroupingError)
    assert not issubclass(GroupingExistsError, GroupingNotFoundError)


def test_the_module_does_not_pull_in_textual() -> None:
    """A fresh interpreter importing the module alone must not load Textual."""
    repo_root = Path(__file__).resolve().parent.parent
    code = "import sys, backend.core.groupings; print('textual' in sys.modules)"

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False"
