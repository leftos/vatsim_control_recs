"""The grouping builder's draft model: members, filters, saves and wording.

No Textual: these tests drive `ui.modals.grouping_draft` directly, which is the
whole point of the module.
"""

import dataclasses
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import ui.modals.grouping_draft as grouping_draft_module
from backend.core.airport_search import AirportSearchIndex
from backend.core.groupings import (
    GroupingExistsError,
    GroupingNameError,
    UserGrouping,
)
from ui.modals.grouping_draft import (
    ASK_TRACK,
    DATA_NOT_LOADED,
    DISCARD_BLANK_CONFIRM,
    OVERWRITE_CONFIRM,
    RENAME_CONFIRM,
    SELF_NESTING,
    SELF_NESTING_MESSAGE,
    TRACK_ADD_LABEL,
    TRACK_CANCEL_LABEL,
    TRACK_OFFER,
    TRACK_SAVE_ONLY_LABEL,
    WRITE,
    BuilderContext,
    BuilderResult,
    DraftError,
    GroupingDraft,
    SavePlan,
    discard_message,
    track_offer_message,
)

IDENTS = frozenset({"KHWD", "KCCR", "KLVK", "KSFO", "KOAK"})
LOADED = AirportSearchIndex({})


def make_draft(
    *,
    items: Any = ("KSFO",),
    filters: Any = None,
    name: str = "Trip",
    original_name: str | None = None,
    groupings: Any = None,
    tracked: Any = ("KSFO",),
    user_names: Any = (),
    preset_names: Any = (),
    airport_idents: Any = IDENTS,
    search_index: AirportSearchIndex | None = LOADED,
) -> GroupingDraft:
    """Build a draft over a small context; every argument has a working default."""
    context = BuilderContext(
        groupings=dict(groupings or {}),
        user_names=frozenset(user_names),
        tracked=frozenset(tracked),
        search_index=search_index,
        preset_names=frozenset(preset_names),
        airport_idents=frozenset(airport_idents),
    )
    return GroupingDraft(
        context,
        items=list(items),
        filters=dict(filters or {}),
        name=name,
        original_name=original_name,
    )


def record_writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Patch the two backend writers; return the calls they receive."""
    calls: list[tuple] = []

    def fake_save(grouping: UserGrouping, **kwargs: Any) -> None:
        calls.append(("save", grouping, kwargs))

    def fake_rename(old: str, grouping: UserGrouping, **kwargs: Any) -> None:
        calls.append(("rename", old, grouping, kwargs))

    monkeypatch.setattr(grouping_draft_module, "save_user_grouping", fake_save)
    monkeypatch.setattr(grouping_draft_module, "rename_user_grouping", fake_rename)
    return calls


# --- the starting members ----------------------------------------------------


def test_init_drops_duplicate_refs_keeping_the_first() -> None:
    draft = make_draft(items=("KSFO", "O90", "KSFO", "KOAK", "O90"))

    assert draft.items == ["KSFO", "O90", "KOAK"]
    assert draft.changed is False


# --- check_name --------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("   ", "a name is required"),
        ("bay area", "that name belongs to a preset"),
        ("ksfo", "that name is an airport ident"),
    ],
)
def test_check_name_refuses_a_blank_preset_or_ident_name(
    name: str, message: str
) -> None:
    draft = make_draft(name=name, preset_names={"Bay Area"})

    with pytest.raises(GroupingNameError, match=message):
        draft.check_name()


def test_check_name_lets_a_stored_or_edited_name_through() -> None:
    edited = make_draft(
        name="bay area", original_name="Bay Area", preset_names={"Bay Area"}
    )
    stored = make_draft(
        name="BAY AREA", user_names={"Bay Area"}, preset_names={"Bay Area"}
    )

    edited.check_name()
    stored.check_name()


# --- add ---------------------------------------------------------------------


def test_add_appends_a_ref_after_the_existing_members() -> None:
    draft = make_draft(items=("KSFO",))

    draft.add("KOAK")

    assert draft.items == ["KSFO", "KOAK"]


def test_add_refuses_a_ref_already_in_the_grouping() -> None:
    draft = make_draft(items=("KSFO",))

    with pytest.raises(DraftError, match="already in this grouping"):
        draft.add("KSFO")

    assert draft.items == ["KSFO"]


def test_add_refuses_the_groupings_own_name() -> None:
    draft = make_draft(items=(), name="Trip")

    with pytest.raises(DraftError, match="can't contain itself"):
        draft.add("Trip")


def test_add_refuses_its_own_name_in_another_case() -> None:
    draft = make_draft(items=(), name="Trip")

    with pytest.raises(DraftError, match="can't contain itself"):
        draft.add("tRiP")


def test_add_refuses_the_name_being_edited_from() -> None:
    draft = make_draft(items=(), name="Renamed", original_name="Trip")

    with pytest.raises(DraftError, match="can't contain itself"):
        draft.add("trip")


def test_add_refuses_a_grouping_that_nests_this_one() -> None:
    groupings = {"Outer": ["Inner", "KSFO"], "Inner": ["Trip", "KOAK"]}
    draft = make_draft(items=(), name="Trip", groupings=groupings)

    with pytest.raises(DraftError, match="can't contain itself"):
        draft.add("Outer")

    assert draft.items == []


def test_add_allows_a_grouping_that_does_not_nest_this_one() -> None:
    groupings = {"Bay Area": ["O90", "KSFO"], "O90": ["KHWD"]}
    draft = make_draft(items=(), name="Trip", groupings=groupings)

    draft.add("Bay Area")

    assert draft.items == ["Bay Area"]


# --- remove ------------------------------------------------------------------


def test_remove_drops_a_direct_item() -> None:
    draft = make_draft(items=("KSFO", "KOAK"))

    draft.remove("KSFO")

    assert draft.items == ["KOAK"]


def test_remove_of_a_ref_that_is_not_an_item_raises() -> None:
    draft = make_draft(items=("KSFO",))

    with pytest.raises(DraftError, match="not in this grouping"):
        draft.remove("KOAK")


# --- exclude -----------------------------------------------------------------


def test_exclude_replaces_the_ref_in_place_with_the_rest_of_its_airports() -> None:
    groupings = {"O90": ["KHWD", "KCCR", "KLVK"]}
    draft = make_draft(
        items=("KSFO", "O90", "KOAK"),
        filters={"KHWD": "arr", "KCCR": "dep", "KSFO": "dep"},
        groupings=groupings,
    )

    draft.exclude("O90", "KHWD")

    assert draft.items == ["KSFO", "KCCR", "KLVK", "KOAK"]
    assert draft.filters == {"KHWD": "arr", "KCCR": "dep", "KSFO": "dep"}
    assert draft.resolved_airports() == ["KSFO", "KCCR", "KLVK", "KOAK"]


def test_exclude_flattens_nested_groupings_and_skips_airports_kept_elsewhere() -> None:
    groupings = {"Outer": ["Inner", "KLVK"], "Inner": ["KCCR", "KHWD"]}
    draft = make_draft(items=("KLVK", "Outer"), groupings=groupings)

    draft.exclude("Outer", "KLVK")

    assert draft.items == ["KLVK", "KCCR", "KHWD"]


def test_exclude_of_a_ref_that_is_not_an_item_raises() -> None:
    draft = make_draft(items=("O90",), groupings={"O90": ["KHWD"]})

    with pytest.raises(DraftError, match="not in this grouping"):
        draft.exclude("KOAK", "KHWD")


def test_exclude_of_an_airport_that_is_not_in_the_grouping_raises() -> None:
    draft = make_draft(items=("O90",), groupings={"O90": ["KHWD"]})

    with pytest.raises(DraftError, match="not in 'O90'"):
        draft.exclude("O90", "KCCR")


def test_exclude_of_a_member_that_is_not_a_grouping_raises() -> None:
    draft = make_draft(items=("KSFO", "O90"), groupings={"O90": ["KHWD", "KCCR"]})

    with pytest.raises(DraftError, match="only a grouping's members can be excluded"):
        draft.exclude("KSFO", "KSFO")


def test_cyclic_groupings_do_not_hang_add_resolve_or_exclude() -> None:
    groupings = {"A": ["B", "KSFO"], "B": ["A", "KOAK"]}
    draft = make_draft(items=(), groupings=groupings)

    draft.add("A")
    assert draft.items == ["A"]
    assert draft.resolved_airports() == ["KOAK", "KSFO"]

    draft.exclude("A", "KOAK")

    assert draft.items == ["KSFO"]


# --- cycle_filter ------------------------------------------------------------


def test_cycle_filter_goes_both_then_dep_then_arr_then_both() -> None:
    draft = make_draft(items=("KSFO",), filters={})

    assert draft.cycle_filter("KSFO") == "dep"
    assert draft.filters == {"KSFO": "dep"}

    assert draft.cycle_filter("KSFO") == "arr"
    assert draft.filters == {"KSFO": "arr"}

    assert draft.cycle_filter("KSFO") is None
    assert draft.filters == {}


# --- resolved_airports and untracked -----------------------------------------


def test_resolved_airports_flattens_nesting_and_drops_an_unknown_ref() -> None:
    groupings = {"Bay Area": ["O90", "KSFO"], "O90": ["KHWD", "KCCR"]}
    draft = make_draft(items=("Bay Area", "NOPE", "KHWD"), groupings=groupings)

    assert draft.resolved_airports() == ["KHWD", "KCCR", "KSFO"]
    assert "NOPE" in draft.items


def test_resolved_airports_normalises_a_lower_case_ident() -> None:
    draft = make_draft(items=("ksfo",))

    assert draft.resolved_airports() == ["KSFO"]


def test_resolved_airports_drops_an_unknown_member_of_a_grouping() -> None:
    draft = make_draft(items=("Bay Area",), groupings={"Bay Area": ["KHWD", "Ghost"]})

    assert draft.resolved_airports() == ["KHWD"]


def test_untracked_lists_the_resolved_airports_the_tracked_list_lacks() -> None:
    groupings = {"O90": ["KHWD", "KCCR"]}
    draft = make_draft(
        items=("O90", "KSFO"), tracked=("KHWD", "KSFO"), groupings=groupings
    )

    assert draft.untracked() == ["KCCR"]


def test_untracked_is_empty_when_the_tracked_list_is_empty() -> None:
    draft = make_draft(items=("KSFO", "KOAK"), tracked=())

    assert draft.untracked() == []


def test_is_tracked_is_true_for_every_airport_when_the_list_is_empty() -> None:
    draft = make_draft(items=("KSFO",), tracked=())

    assert draft.is_tracked("KJFK") is True


def test_is_tracked_marks_an_airport_the_list_lacks() -> None:
    draft = make_draft(items=("KSFO",), tracked=("KSFO",))

    assert draft.is_tracked("KSFO") is True
    assert draft.is_tracked("KOAK") is False


# --- changed -----------------------------------------------------------------


def test_changed_is_false_at_the_start() -> None:
    draft = make_draft(items=("KSFO",), filters={"KSFO": "dep"}, name="Trip")

    assert draft.changed is False


def test_changed_is_true_after_an_add_and_false_again_after_removing_it() -> None:
    draft = make_draft(items=("KSFO",))

    draft.add("KOAK")
    assert draft.changed is True

    draft.remove("KOAK")
    assert draft.changed is False


def test_changed_counts_a_rename_and_ignores_surrounding_space() -> None:
    draft = make_draft(items=("KSFO",), name="Trip")

    draft.name = "  Trip  "
    assert draft.changed is False

    draft.name = "Trips"
    assert draft.changed is True


def test_changed_is_false_after_a_filter_cycles_all_the_way_back() -> None:
    draft = make_draft(items=("KSFO",))

    for _ in range(3):
        draft.cycle_filter("KSFO")

    assert draft.changed is False


def test_changed_is_true_after_an_exclude() -> None:
    draft = make_draft(items=("O90",), groupings={"O90": ["KHWD", "KCCR"]})

    draft.exclude("O90", "KHWD")

    assert draft.changed is True


# --- to_grouping -------------------------------------------------------------


def test_to_grouping_drops_the_filter_of_an_airport_no_longer_there() -> None:
    groupings = {"O90": ["KHWD", "KCCR"]}
    draft = make_draft(
        items=("O90",),
        filters={"KHWD": "arr", "KCCR": "dep", "KOAK": "dep"},
        groupings=groupings,
    )

    assert draft.to_grouping() == UserGrouping(
        name="Trip", items=("O90",), filters={"KHWD": "arr", "KCCR": "dep"}
    )


def test_to_grouping_strips_the_name_and_keeps_the_member_order() -> None:
    draft = make_draft(items=("KOAK", "KSFO"), name="  Trip  ")

    assert draft.to_grouping() == UserGrouping(
        name="Trip", items=("KOAK", "KSFO"), filters={}
    )


# --- save_plan ---------------------------------------------------------------


def test_save_plan_writes_when_the_data_is_loaded_and_all_are_tracked() -> None:
    draft = make_draft(items=("KSFO",), tracked=("KSFO",))

    assert draft.save_plan() == SavePlan(kind=WRITE)


def test_save_plan_asks_to_track_the_untracked_airports() -> None:
    draft = make_draft(items=("KSFO", "KOAK"), tracked=("KSFO",))

    assert draft.save_plan() == SavePlan(kind=ASK_TRACK, untracked=("KOAK",))


def test_save_plan_warns_while_the_airport_data_is_not_loaded() -> None:
    draft = make_draft(items=("KSFO",), tracked=(), search_index=None)

    assert draft.save_plan() == SavePlan(kind=DATA_NOT_LOADED)


def test_save_plan_warns_first_when_the_data_is_not_loaded_and_members_untracked() -> (
    None
):
    draft = make_draft(items=("KSFO",), tracked=("KOAK",), search_index=None)

    assert draft.save_plan().kind == DATA_NOT_LOADED


def test_save_plan_refuses_a_name_that_nests_one_of_the_members() -> None:
    draft = make_draft(items=(), name="", groupings={"Outer": ["Trip", "KOAK"]})

    draft.add("Outer")
    draft.name = "Trip"

    assert draft.save_plan() == SavePlan(kind=SELF_NESTING, item="Outer")


def test_save_plan_refuses_renaming_an_edited_grouping_onto_a_nested_name() -> None:
    groupings = {"Outer": ["Beta", "KOAK"]}
    draft = make_draft(
        items=("Outer",), name="Beta", original_name="Alpha", groupings=groupings
    )

    assert draft.save_plan() == SavePlan(kind=SELF_NESTING, item="Outer")


# --- write -------------------------------------------------------------------


def test_write_saves_a_new_grouping_with_the_contexts_name_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = record_writes(monkeypatch)
    draft = make_draft(
        items=("KSFO",),
        name="  Trip  ",
        preset_names={"Bay Area"},
        airport_idents=IDENTS | {"SFO"},
    )

    saved = draft.write(overwrite=False)

    assert saved == UserGrouping(name="Trip", items=("KSFO",), filters={})
    assert calls == [
        (
            "save",
            UserGrouping(name="Trip", items=("KSFO",), filters={}),
            {
                "overwrite": False,
                "preset_names": frozenset({"Bay Area"}),
                "airport_idents": frozenset(IDENTS | {"SFO"}),
            },
        )
    ]


def test_write_renames_while_editing_even_with_an_unchanged_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = record_writes(monkeypatch)
    draft = make_draft(items=("KSFO",), name="Trip", original_name="Trip")

    draft.write(overwrite=True)

    assert calls[0][0] == "rename"
    assert calls[0][1] == "Trip"
    assert calls[0][2] == UserGrouping(name="Trip", items=("KSFO",), filters={})
    assert calls[0][3] == {
        "overwrite": True,
        "preset_names": frozenset(),
        "airport_idents": IDENTS,
    }


def test_write_renames_a_case_only_change(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = record_writes(monkeypatch)
    draft = make_draft(items=("KSFO",), name="trip", original_name="Trip")

    draft.write(overwrite=False)

    assert calls[0][1] == "Trip"
    assert calls[0][2].name == "trip"


def test_write_lets_a_grouping_error_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(grouping: UserGrouping, **kwargs: Any) -> None:
        raise GroupingExistsError("'Trip' is already one of my groupings")

    monkeypatch.setattr(grouping_draft_module, "save_user_grouping", boom)
    draft = make_draft(items=("KSFO",))

    with pytest.raises(GroupingExistsError, match="already one of my groupings"):
        draft.write(overwrite=False)


# --- BuilderResult -----------------------------------------------------------


def test_builder_result_carries_what_the_caller_applies() -> None:
    saved = UserGrouping(name="Trip", items=("KSFO",), filters={})

    result = BuilderResult(saved=saved, original_name="Old", track=("KOAK",))

    assert result.saved == saved
    assert result.original_name == "Old"
    assert result.track == ("KOAK",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.track = ()


# --- the wording the screen shows --------------------------------------------


def test_the_confirm_templates_carry_the_owner_wording() -> None:
    assert OVERWRITE_CONFIRM.format(name="Trip") == (
        "'Trip' is already one of my groupings. Replace it?"
    )
    assert RENAME_CONFIRM.format(new="B", old="A") == (
        "'B' is already one of my groupings. Replace it with this one? ('A' is removed)"
    )
    assert (TRACK_ADD_LABEL, TRACK_SAVE_ONLY_LABEL, TRACK_CANCEL_LABEL) == (
        "Add and save",
        "Save only",
        "Cancel",
    )
    assert SELF_NESTING_MESSAGE == "a grouping can't contain itself"


def test_discard_message_names_the_grouping() -> None:
    assert discard_message("Trip") == "Discard your changes to 'Trip'?"


def test_discard_message_falls_back_to_the_name_being_edited() -> None:
    assert discard_message("  ", "Trip") == "Discard your changes to 'Trip'?"
    assert discard_message("Trip", "Alpha") == "Discard your changes to 'Trip'?"


def test_discard_message_says_this_grouping_when_there_is_no_name() -> None:
    assert discard_message("   ") == DISCARD_BLANK_CONFIRM
    assert discard_message("   ", None) == DISCARD_BLANK_CONFIRM
    assert "this grouping" in DISCARD_BLANK_CONFIRM


def test_track_offer_lists_every_icao_while_there_are_six_or_fewer() -> None:
    assert track_offer_message(["KHWD", "KCCR"]) == (
        "2 of these aren't tracked (KHWD, KCCR). Add them to your tracked airports?"
    )
    assert track_offer_message([f"K{n:03d}" for n in range(6)]) == TRACK_OFFER.format(
        n=6, icaos="K000, K001, K002, K003, K004, K005"
    )


def test_track_offer_counts_the_rest_beyond_six() -> None:
    assert track_offer_message([f"K{n:03d}" for n in range(8)]) == (
        "8 of these aren't tracked (K000, K001, K002, K003, K004, K005, +2). "
        "Add them to your tracked airports?"
    )


# --- the module itself -------------------------------------------------------


def test_the_module_does_not_pull_in_textual() -> None:
    """A fresh interpreter loading the module by path alone must not load Textual."""
    repo_root = Path(__file__).resolve().parent.parent
    code = (
        "import importlib.util, sys; "
        "spec = importlib.util.spec_from_file_location("
        "'grouping_draft', 'ui/modals/grouping_draft.py'); "
        "module = importlib.util.module_from_spec(spec); "
        "spec.loader.exec_module(module); "
        "print('textual' in sys.modules)"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False"
