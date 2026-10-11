"""The grouping builder: name a ★ grouping, add to it, edit its members, save it.

The screen drives a `GroupingDraft`: the draft holds the members, their filters
and the save rules, and the screen owns the widgets, the keys and the confirms.
It writes through the draft, shows the backend's errors itself, and dismisses
with a `BuilderResult` for its caller to apply, or None when cancelled.

From 110 columns the Add pane and the Members pane sit side by side; below that
one pane shows at a time and Tab flips between them.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.style import Style
from textual.visual import RenderOptions, RulesMap, Visual
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option, OptionDoesNotExist

from backend.core.airport_search import AirportHit
from backend.core.groupings import (
    GroupingError,
    GroupingExistsError,
    GroupingNameError,
)
from ui import config
from ui.modals.confirm_modal import Choice, ChoiceModal, ConfirmModal
from ui.modals.grouping_draft import (
    ASK_TRACK,
    BAD_NAME,
    DATA_NOT_LOADED,
    OVERWRITE_CONFIRM,
    RENAME_CONFIRM,
    SELF_NESTING,
    SELF_NESTING_MESSAGE,
    TRACK_ADD_LABEL,
    TRACK_CANCEL_LABEL,
    TRACK_SAVE_ONLY_LABEL,
    BuilderContext,
    BuilderResult,
    DraftError,
    GroupingDraft,
    SavePlan,
    discard_message,
    track_offer_message,
)

# The terminal width from which the two panes sit side by side.
_WIDE_FROM = 110

# The most result rows the Add list shows before its "type to narrow" row.
_MAX_RESULTS = 50

_SEARCH_PROMPT = "type to search airports and groupings"
_LOADING_ROW = "airport data is still loading"
_NO_MATCHES_ROW = "no airports or groupings match"
_CAP_ROW = "showing {shown} of {total} — type to narrow"
_NO_MEMBERS_ROW = "no members yet: add airports or groupings"
_COLLAPSED_ROW = "     ▸ {count} airports (→ to expand)"
_DATA_LOADING_WARNING = "airport data is still loading; try again in a moment"

# The mark column before an airport's ident, and the hint's legend for it.
_UNTRACKED = "○ "
_TRACKED = "  "
_LEGEND = "○ not tracked"
_STARRED = "★ "

_HINT_SEPARATOR = " · "
_WIDE_TAB_HINT = "Tab next pane"

# The hint shows at most this many items, so it fits one line from 80 columns.
# "Ctrl+S save", "Esc cancel" and the legend always stay; past the limit the
# Tab item gives way first, then the row keys below in this order.
_HINT_MAX_ITEMS = 6
_HINT_DROP_ORDER = ("← collapse", "→ expand", "F dep/arr/both")

# Query prefixes that narrow the Add search to one kind, as in Go To.
_QUERY_PREFIXES = {"@": "airport", "$": "grouping"}

_TRACK_CHOICES = (
    Choice(key="1", label=TRACK_ADD_LABEL, value="add"),
    Choice(key="2", label=TRACK_SAVE_ONLY_LABEL, value="save"),
    Choice(key="3", label=TRACK_CANCEL_LABEL, value="cancel"),
)

# The hint items of each Members row kind; a grouping row's depend on its state.
_MEMBER_HINTS = {
    "airport": ["Del remove", "F dep/arr/both"],
    "collapsed": ["→ expand"],
    "sub": ["Del exclude", "F dep/arr/both", "← collapse"],
    "unknown": ["Del remove"],
}

# Why F cannot act on a row that is not an airport.
_NOT_AN_AIRPORT = {
    "grouping": "the highlighted row is a grouping (→ expands its airports)",
    "collapsed": "the highlighted row is a grouping's airports (→ expands them)",
    "unknown": "the highlighted row is not a known airport or grouping",
}


@dataclass(frozen=True)
class _MemberRow:
    """What one Members row stands for.

    Attributes:
        kind: "airport" or "grouping" for a member, "unknown" for a member that
            is neither a grouping nor a known airport, "collapsed" for a nested
            grouping's folded airports, "sub" for one of its expanded airports.
        ref: The member the row belongs to.
        icao: The airport of an "airport" or "sub" row, upper-cased.
    """

    kind: str
    ref: str
    icao: str = ""


@dataclass(frozen=True, eq=False)
class _OneLine(Visual):
    """A list row that never wraps: past the list's width its body is cut with "…".

    The head (the mark and ident) and the tail (a filter or a tag such as
    "member") always show; `plain` is the whole row, uncut.
    """

    head: Content
    body: Content
    tail: Content

    @property
    def plain(self) -> str:
        return self._whole().plain

    def with_tail(self, tag: Content) -> "_OneLine":
        """The same row with a tag appended to its tail."""
        return replace(self, tail=self.tail + tag)

    def render_strips(
        self, width: int, height: int | None, style: Style, options: RenderOptions
    ) -> list[Strip]:
        return self._fit(width).render_strips(width, height, style, options)

    def get_optimal_width(self, rules: RulesMap, container_width: int) -> int:
        return self._whole().cell_length

    def get_height(self, rules: RulesMap, width: int) -> int:
        return 1

    def _whole(self) -> Content:
        return self.head + self.body + self.tail

    def _fit(self, width: int) -> Content:
        """The row cut to `width` cells, the body losing its end first."""
        room = width - self.head.cell_length - self.tail.cell_length
        body = self.body
        if body.cell_length > room:
            body = body.truncate(max(room, 0), ellipsis=True)
        return (self.head + body + self.tail).truncate(width)


def _split_query(query: str) -> tuple[str | None, str]:
    """Split a leading @ or $ off a query; return its kind and the rest."""
    for prefix, kind in _QUERY_PREFIXES.items():
        if query.startswith(prefix):
            return kind, query[1:].strip()
    return None, query


def _dim_row(text: str) -> Option:
    """A dim row the cursor skips."""
    return Option(Content.styled(text, "dim"), disabled=True)


def _ref_of(option_id: str) -> str:
    """The member ref an Add result's option id stands for."""
    return option_id.split(":", 1)[1]


def _airport_name(icao: str) -> str:
    """The airport's name from the unified data, or empty when it has none."""
    info = (config.UNIFIED_AIRPORT_DATA or {}).get(icao)
    name = info.get("name") if isinstance(info, dict) else None
    return name.strip() if isinstance(name, str) else ""


def _display_names(raw_names: Mapping[str, str]) -> dict[str, str]:
    """Each airport's display name, as Go To and the main table show it.

    One batch call on the disambiguator covers every airport. An airport it does
    not name (no entry, an empty name, or its own ident back) keeps its raw name,
    as does every airport while there is no disambiguator.

    Args:
        raw_names: Each airport's ident to its name in the unified data.

    Returns:
        Each ident to the name its row shows.
    """
    disambiguator = config.DISAMBIGUATOR
    if disambiguator is None or not raw_names:
        return dict(raw_names)
    found = disambiguator.get_full_names_batch(list(raw_names))
    names: dict[str, str] = {}
    for icao, raw in raw_names.items():
        name = found.get(icao)
        names[icao] = name if name and name != icao else raw
    return names


def _fit_hint(actions: list[str], fixed: list[str]) -> list[str]:
    """Fit the row keys and the Tab item (last of `actions`) beside `fixed`.

    Drops the Tab item, then the row keys in `_HINT_DROP_ORDER`, until at most
    `_HINT_MAX_ITEMS` remain.
    """
    for drop in (actions[-1], *_HINT_DROP_ORDER):
        if len(actions) + len(fixed) <= _HINT_MAX_ITEMS:
            break
        actions = [action for action in actions if action != drop]
    return [*actions, *fixed]


def _first_enabled(option_list: OptionList) -> int | None:
    """The index of the first row the cursor can rest on."""
    for index, option in enumerate(option_list.options):
        if not option.disabled:
            return index
    return None


def _option_index(option_list: OptionList, option_id: str | None) -> int | None:
    """The index of the row with this id, or None when there is none."""
    if option_id is None:
        return None
    try:
        return option_list.get_option_index(option_id)
    except OptionDoesNotExist:
        return None


def _highlight(option_list: OptionList, preferred: int | None) -> None:
    """Highlight `preferred`, clamped to the list, else the first enabled row."""
    count = option_list.option_count
    if preferred is not None and count:
        index = min(preferred, count - 1)
        if not option_list.get_option_at_index(index).disabled:
            option_list.highlighted = index
            return
    option_list.highlighted = _first_enabled(option_list)


class GroupingBuilderScreen(ModalScreen[BuilderResult | None]):
    """Build or edit one of my groupings: its name, members and their filters."""

    CSS = """
    GroupingBuilderScreen {
        align: center middle;
    }

    #builder-container {
        width: 96;
        max-width: 100%;
        height: 90%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    GroupingBuilderScreen.-wide #builder-container {
        width: 110;
    }

    #builder-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #builder-name-row {
        height: auto;
    }

    #builder-name-label {
        width: 6;
        height: 3;
        content-align: left middle;
        text-style: bold;
    }

    #builder-name {
        width: 1fr;
    }

    #builder-name-error {
        display: none;
        color: $error;
        padding-left: 6;
    }

    #builder-panes {
        height: 1fr;
        margin-top: 1;
    }

    #builder-add-pane, #builder-members-pane {
        width: 1fr;
        height: 1fr;
    }

    GroupingBuilderScreen.-wide #builder-add-pane {
        margin-right: 1;
    }

    GroupingBuilderScreen.-narrow #builder-add-pane {
        display: none;
    }

    GroupingBuilderScreen.-narrow.-show-add #builder-add-pane {
        display: block;
    }

    GroupingBuilderScreen.-narrow.-show-add #builder-members-pane {
        display: none;
    }

    .builder-pane-title {
        color: $text-muted;
        text-style: bold;
    }

    #builder-results, #builder-members {
        height: 1fr;
    }

    #builder-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(
        self,
        context: BuilderContext,
        *,
        items: Sequence[str],
        filters: Mapping[str, str],
        name: str,
        original_name: str | None,
    ) -> None:
        """Open the builder on a draft of these members.

        Args:
            context: What the draft reads but does not own.
            items: The member refs in order (airport idents and grouping names).
            filters: Airport ident to "dep" or "arr".
            name: The name the Name field starts with.
            original_name: The ★ grouping being edited, or None for a new one.
        """
        super().__init__()
        self.context = context
        self.draft = GroupingDraft(
            context,
            items=items,
            filters=filters,
            name=name,
            original_name=original_name,
        )
        # Nested groupings whose airports show as rows instead of one folded row
        self._expanded: set[str] = set()
        # What each Members row stands for, 1:1 with its options
        self._member_rows: list[_MemberRow | None] = []
        # The pane a narrow terminal shows: "members" or "add"
        self._pane = "members"
        self._members_marked = False
        self._results_marked = False
        # The display name of each airport a rendered row has shown
        self._shown_names: dict[str, str] = {}
        # The last search: its box text, query, loading flag, matches and total
        self._matches: tuple[str, str, bool, list[AirportHit | str], int] | None = None

    # --- layout ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        original = self.draft.original_name
        title = "New grouping" if original is None else f"Edit grouping ★ {original}"
        with Container(id="builder-container"):
            yield Static(Content(title), id="builder-title")
            with Horizontal(id="builder-name-row"):
                yield Static("Name", id="builder-name-label")
                yield Input(
                    value=self.draft.name,
                    placeholder="grouping name",
                    id="builder-name",
                    select_on_focus=False,
                )
            yield Static("", id="builder-name-error")
            with Horizontal(id="builder-panes"):
                with Vertical(id="builder-add-pane"):
                    yield Static(
                        Content("Add · any airport or grouping"),
                        id="builder-add-title",
                        classes="builder-pane-title",
                    )
                    yield Input(
                        placeholder="Search... (@airport, $grouping)",
                        id="builder-search",
                        select_on_focus=False,
                    )
                    yield OptionList(id="builder-results")
                with Vertical(id="builder-members-pane"):
                    yield Static(
                        "", id="builder-members-title", classes="builder-pane-title"
                    )
                    yield OptionList(id="builder-members")
            yield Static("", id="builder-hint")

    def on_mount(self) -> None:
        self._apply_layout()
        self._render_results()
        self._render_members()
        self._focus_pane()

    def on_resize(self, _event: events.Resize) -> None:
        self._apply_layout()

    def on_screen_resume(self, _event: events.ScreenResume) -> None:
        # A resize while a confirm covers the builder reaches only the confirm
        self._apply_layout()

    def _apply_layout(self) -> None:
        """Lay out for the terminal width; narrow shows the pane holding focus."""
        if not self.is_mounted:
            return
        wide = self._is_wide()
        self.set_class(wide, "-wide")
        self.set_class(not wide, "-narrow")
        if not wide:
            self._show_focused_pane()
        self._update_hint()

    def _show_focused_pane(self) -> None:
        """Show the pane the focused widget is in; the Name field keeps the pane."""
        focused = self.focused
        if focused in (self._search, self._results):
            self._pane = "add"
        elif focused is self._members:
            self._pane = "members"
        self.set_class(self._pane == "add", "-show-add")

    @property
    def _name_input(self) -> Input:
        return self.query_one("#builder-name", Input)

    @property
    def _search(self) -> Input:
        return self.query_one("#builder-search", Input)

    @property
    def _results(self) -> OptionList:
        return self.query_one("#builder-results", OptionList)

    @property
    def _members(self) -> OptionList:
        return self.query_one("#builder-members", OptionList)

    def _is_wide(self) -> bool:
        """Whether the terminal is wide enough for both panes."""
        return self.app.size.width >= _WIDE_FROM

    def _focus_pane(self) -> None:
        """Focus the Add search (wide, or Add shown) or else the Members list."""
        if self._is_wide() or self._pane == "add":
            self._search.focus()
        else:
            self._members.focus()

    # --- keys -----------------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        """Dispatch the builder's keys; the hint line mirrors these handlers."""
        if self._handle_key(event):
            event.prevent_default()
            event.stop()

    def _handle_key(self, event: events.Key) -> bool:
        if event.key == "ctrl+s":
            self._save()
            return True
        if event.key == "tab":
            self._handle_tab()
            return True
        if self._members.has_focus and self._handle_member_key(event.key):
            return True
        if self._results.has_focus and self._type_into_search(event):
            return True
        return self._handle_focus_move(event.key)

    def _handle_tab(self) -> None:
        """Wide: Name, Add, Members in turn. Narrow: flip the shown pane."""
        if self._is_wide():
            self._focus_next_stop()
            return
        if self.focused is not self._name_input:
            self._pane = "add" if self._pane == "members" else "members"
            self.set_class(self._pane == "add", "-show-add")
        self._focus_pane()
        self._update_hint()

    def _focus_next_stop(self) -> None:
        stops = [self._name_input, self._search, self._members]
        focused = self._search if self._results.has_focus else self.focused
        index = stops.index(focused) if focused in stops else -1
        stops[(index + 1) % len(stops)].focus()

    def _handle_member_key(self, key: str) -> bool:
        """Del, F, → and ← on the highlighted Members row."""
        actions = {
            "delete": self._delete_member,
            "f": self._cycle_member_filter,
            "right": self._expand_member,
            "left": self._collapse_member,
        }
        action = actions.get(key)
        if action is None:
            return False
        action()
        return True

    def _type_into_search(self, event: events.Key) -> bool:
        """A printable key in the Add results goes to the search box."""
        if not (event.is_printable and event.character):
            return False
        search = self._search
        search.focus()
        search.value += event.character
        search.cursor_position = len(search.value)
        return True

    def _handle_focus_move(self, key: str) -> bool:
        """Up and Down between the Name field, the search box and the lists."""
        if key not in ("up", "down"):
            return False
        focused = self.focused
        if focused is self._name_input:
            return self._move_from_name(key)
        if focused is self._search:
            return self._move_from_search(key)
        if isinstance(focused, OptionList) and key == "up":
            return self._move_up_from_list(focused)
        return False

    def _move_from_name(self, key: str) -> bool:
        if key != "down":
            return False
        self._focus_pane()
        return True

    def _move_from_search(self, key: str) -> bool:
        if key == "up":
            self._name_input.focus()
            return True
        if self._results.highlighted is None:
            return False
        self._results.focus()
        return True

    def _move_up_from_list(self, option_list: OptionList) -> bool:
        """Up from a list's first row reaches the search box or the Name field."""
        first = _first_enabled(option_list)
        if first is not None and option_list.highlighted not in (None, first):
            return False
        target = self._search if option_list is self._results else self._name_input
        target.focus()
        return True

    # --- the Add search ------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "builder-name":
            self.draft.name = event.value
            self._show_name_error("")
        elif event.input.id == "builder-search":
            self._render_results()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "builder-search":
            self._add_highlighted_result()
        elif event.input.id == "builder-name":
            self._focus_pane()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option_list.id == "builder-results":
            self._add_highlighted_result()

    def on_option_list_option_highlighted(
        self, _event: OptionList.OptionHighlighted
    ) -> None:
        self._update_hint()

    def on_descendant_focus(self, _event: events.DescendantFocus) -> None:
        self._update_hint()

    def _render_results(self) -> None:
        """Rebuild the Add list from the search box's query."""
        option_list = self._results
        highlighted = option_list.highlighted_option
        previous = None if highlighted is None else highlighted.id

        _, query, loading, found, total = self._current_matches()
        hits = [item for item in found[:_MAX_RESULTS] if isinstance(item, AirportHit)]
        self._results_marked = any(not self.draft.is_tracked(hit.ident) for hit in hits)
        self._shown_names.update(_display_names({hit.ident: hit.name for hit in hits}))

        option_list.clear_options()
        option_list.add_options(self._result_options(query, loading, found, total))
        _highlight(option_list, _option_index(option_list, previous))
        self._update_hint()

    def _current_matches(self) -> tuple[str, str, bool, list[AirportHit | str], int]:
        """The search box's matches, searched again only when its text changed.

        Returns:
            The box text, the query without its prefix, whether the airport data
            is still loading, the matches (at most `_MAX_RESULTS` airports, then
            every matching grouping) and the count of every match.
        """
        text = self._search.value.strip()
        if self._matches is None or self._matches[0] != text:
            kind, query = _split_query(text)
            loading, found, total = (
                self._search_for(kind, query) if query else (False, [], 0)
            )
            self._matches = (text, query, loading, found, total)
        return self._matches

    def _search_for(
        self, kind: str | None, query: str
    ) -> tuple[bool, list[AirportHit | str], int]:
        """The capped airport hits, then every matching grouping name.

        Returns:
            Whether the airport data is still loading, the matches, and the
            count of every match.
        """
        found: list[AirportHit | str] = []
        total = 0
        loading = False
        if kind != "grouping":
            index = self.context.search_index
            if index is None:
                loading = True
            else:
                hits, total = index.search_with_total(
                    query, self.context.tracked, _MAX_RESULTS
                )
                found.extend(hits)
        if kind != "airport":
            names = self._matching_groupings(query)
            found.extend(names)
            total += len(names)
        return loading, found, total

    def _matching_groupings(self, query: str) -> list[str]:
        """Grouping names containing the query, ★ ones first, each by name."""
        needle = query.lower()
        names = [name for name in self.context.groupings if needle in name.lower()]
        starred = sorted(name for name in names if name in self.context.user_names)
        others = sorted(name for name in names if name not in self.context.user_names)
        return [*starred, *others]

    def _result_options(
        self, query: str, loading: bool, found: list[AirportHit | str], total: int
    ) -> list[Option]:
        if not query:
            return [_dim_row(_SEARCH_PROMPT)]
        options = [_dim_row(_LOADING_ROW)] if loading else []
        options.extend(self._result_option(item) for item in found[:_MAX_RESULTS])
        if total > _MAX_RESULTS:
            cap = _CAP_ROW.format(shown=_MAX_RESULTS, total=total)
            options.append(_dim_row(cap))
        if not options:
            options.append(_dim_row(_NO_MATCHES_ROW))
        return options

    def _result_option(self, item: AirportHit | str) -> Option:
        if isinstance(item, AirportHit):
            ref, option_id = item.ident, f"airport:{item.ident}"
            label = self._hit_label(item)
        else:
            ref, option_id = item, f"grouping:{item}"
            label = self._grouping_label(item)
        if ref in self.draft.items:
            label = label.with_tail(Content.styled("  member", "dim"))
        return Option(label, id=option_id)

    def _hit_label(self, hit: AirportHit) -> _OneLine:
        mark = _TRACKED if self.draft.is_tracked(hit.ident) else _UNTRACKED
        body = self._shown_names.get(hit.ident, hit.name)
        if hit.city:
            body += f" · {hit.city}"
        return _OneLine(Content(f"{mark}{hit.ident:<5} "), Content(body), Content(""))

    def _add_highlighted_result(self) -> None:
        """Add the highlighted Add result to the members."""
        option = self._results.highlighted_option
        if option is None or option.disabled or option.id is None:
            self._warn(
                "Enter adds the highlighted result; type to search airports and groupings"
            )
            return
        self._add_ref(_ref_of(option.id))

    def _add_ref(self, ref: str) -> None:
        """Add a member; a ref the draft refuses (already in, or this grouping) warns."""
        try:
            self.draft.add(ref)
        except DraftError as error:
            self._warn(str(error))
            return
        self._render_members(focus_id=f"ref:{ref}")
        self._render_results()

    # --- the Members list ----------------------------------------------------

    def _render_members(self, focus_id: str | None = None) -> None:
        """Rebuild the Members list, keeping the highlight where it can.

        Args:
            focus_id: The row to highlight; by default the highlighted row, else
                the row now at its index.
        """
        option_list = self._members
        old_index = option_list.highlighted
        highlighted = option_list.highlighted_option
        if focus_id is None and highlighted is not None:
            focus_id = highlighted.id

        self._shown_names.update(_display_names(self._shown_member_airports()))
        pairs = self._member_pairs()
        self._member_rows = [row for _, row in pairs]
        self._members_marked = any(
            row is not None and row.icao and not self.draft.is_tracked(row.icao)
            for row in self._member_rows
        )
        option_list.clear_options()
        option_list.add_options([option for option, _ in pairs])

        index = _option_index(option_list, focus_id)
        _highlight(option_list, old_index if index is None else index)
        self._update_members_title()
        self._update_hint()

    def _shown_member_airports(self) -> dict[str, str]:
        """Each known airport a Members row shows, to its unified-data name."""
        icaos: list[str] = []
        for ref in self.draft.items:
            if ref not in self.context.groupings:
                icaos.append(ref.strip().upper())
            elif ref in self._expanded:
                icaos.extend(self.draft.airports_of(ref))
        known = self.context.airport_idents
        return {icao: _airport_name(icao) for icao in icaos if icao in known}

    def _member_pairs(self) -> list[tuple[Option, _MemberRow | None]]:
        """Each Members option with what it stands for, in member order."""
        pairs: list[tuple[Option, _MemberRow | None]] = []
        for ref in self.draft.items:
            if ref in self.context.groupings:
                pairs.extend(self._grouping_pairs(ref))
            else:
                pairs.append(self._airport_pair(ref))
        if not pairs:
            pairs.append((_dim_row(_NO_MEMBERS_ROW), None))
        return pairs

    def _airport_pair(self, ref: str) -> tuple[Option, _MemberRow | None]:
        """An airport member's row; a ref the data does not know shows as written."""
        icao = ref.strip().upper()
        if self.context.search_index is None or icao in self.context.airport_idents:
            option = Option(self._airport_label(icao), id=f"ref:{ref}")
            return option, _MemberRow("airport", ref, icao)
        label = _OneLine(
            Content(_TRACKED), Content(ref), Content.styled("  unknown", "dim")
        )
        return Option(label, id=f"ref:{ref}"), _MemberRow("unknown", ref)

    def _grouping_pairs(self, ref: str) -> list[tuple[Option, _MemberRow | None]]:
        """A nested grouping's row, then its folded row or one row per airport."""
        pairs: list[tuple[Option, _MemberRow | None]] = [
            (
                Option(self._grouping_label(ref), id=f"ref:{ref}"),
                _MemberRow("grouping", ref),
            )
        ]
        airports = self.draft.airports_of(ref)
        if not airports:
            return pairs
        if ref not in self._expanded:
            label = Content(_COLLAPSED_ROW.format(count=len(airports)))
            pairs.append(
                (Option(label, id=f"collapsed:{ref}"), _MemberRow("collapsed", ref))
            )
            return pairs
        for icao in airports:
            option = Option(self._airport_label(icao, "    "), id=f"sub:{ref}:{icao}")
            pairs.append((option, _MemberRow("sub", ref, icao)))
        return pairs

    def _airport_label(self, icao: str, indent: str = "") -> _OneLine:
        mark = _TRACKED if self.draft.is_tracked(icao) else _UNTRACKED
        head = f"{indent}{mark}{icao:<5}"
        name = self._shown_names.get(icao) or _airport_name(icao)
        body = f" {name}" if name else ""
        if not body:
            head = head.rstrip()
        direction = self.draft.filters.get(icao)
        tail = f"  {direction}" if direction else ""
        return _OneLine(Content(head), Content(body), Content(tail))

    def _grouping_label(self, name: str) -> _OneLine:
        mark = _STARRED if name in self.context.user_names else _TRACKED
        return _OneLine(
            Content(mark), Content(name), Content.styled("  grouping", "dim")
        )

    def _update_members_title(self) -> None:
        members = len(self.draft.items)
        airports = len(self.draft.resolved_airports())
        title = f"Members · {members} → {airports} airports"
        self.query_one("#builder-members-title", Static).update(Content(title))

    def _highlighted_member(self) -> _MemberRow | None:
        index = self._members.highlighted
        if index is None or index >= len(self._member_rows):
            return None
        return self._member_rows[index]

    def _warn_no_member(self, key: str) -> None:
        self._warn(f"{key} acts on the highlighted member; there are no members yet")

    def _delete_member(self) -> None:
        """Del: remove a member, or exclude one airport of a nested grouping."""
        row = self._highlighted_member()
        if row is None:
            self._warn_no_member("Del")
            return
        if row.kind == "collapsed":
            self._warn(f"Del removes a member; {_NOT_AN_AIRPORT['collapsed']}")
            return
        try:
            if row.kind == "sub":
                self.draft.exclude(row.ref, row.icao)
            else:
                self.draft.remove(row.ref)
        except DraftError as error:
            self._error(str(error))
            return
        self._expanded.discard(row.ref)
        self._render_members()
        self._render_results()

    def _cycle_member_filter(self) -> None:
        """F: cycle an airport row's filter both → dep → arr → both."""
        row = self._highlighted_member()
        if row is None:
            self._warn_no_member("F")
            return
        if row.kind in _NOT_AN_AIRPORT:
            self._warn(f"F cycles an airport's filter; {_NOT_AN_AIRPORT[row.kind]}")
            return
        self.draft.cycle_filter(row.icao)
        self._render_members()

    def _expand_member(self) -> None:
        """→: show a nested grouping's airports as rows of their own."""
        row = self._highlighted_member()
        if row is None:
            self._warn_no_member("→")
            return
        if row.kind in ("airport", "sub"):
            self._warn("→ expands a nested grouping; the highlighted row is an airport")
            return
        airports = self.draft.airports_of(row.ref)
        if not airports:
            self._warn(f"→ expands a nested grouping; '{row.ref}' has no airports")
            return
        if row.ref in self._expanded:
            return
        self._expanded.add(row.ref)
        self._render_members(focus_id=f"sub:{row.ref}:{airports[0]}")

    def _collapse_member(self) -> None:
        """←: fold a nested grouping's airports back under its row."""
        row = self._highlighted_member()
        if row is None:
            self._warn_no_member("←")
            return
        if row.kind in ("airport", "unknown"):
            self._warn(
                "← collapses a nested grouping; the highlighted row is not in one"
            )
            return
        if row.ref not in self._expanded:
            return
        self._expanded.discard(row.ref)
        self._render_members(focus_id=f"ref:{row.ref}")

    # --- the hint line -------------------------------------------------------

    def _update_hint(self) -> None:
        """Show the keys that act on the focused row, then the screen's keys."""
        fixed = ["Ctrl+S save", "Esc cancel"]
        if self._legend_shown():
            fixed.append(_LEGEND)
        items = _fit_hint([*self._row_hint(), self._tab_hint()], fixed)
        hint = self.query_one("#builder-hint", Static)
        hint.update(Content(_HINT_SEPARATOR.join(items)))

    def _row_hint(self) -> list[str]:
        focused = self.focused
        if focused is self._members:
            return self._member_hint()
        if focused in (self._search, self._results) and self._result_addable():
            return ["Enter add"]
        return []

    def _member_hint(self) -> list[str]:
        row = self._highlighted_member()
        if row is None:
            return []
        if row.kind != "grouping":
            return list(_MEMBER_HINTS[row.kind])
        if row.ref in self._expanded:
            return ["Del remove", "← collapse"]
        if self.draft.airports_of(row.ref):
            return ["Del remove", "→ expand"]
        return ["Del remove"]

    def _result_addable(self) -> bool:
        option = self._results.highlighted_option
        if option is None or option.disabled or option.id is None:
            return False
        return _ref_of(option.id) not in self.draft.items

    def _tab_hint(self) -> str:
        if self._is_wide():
            return _WIDE_TAB_HINT
        return "Tab Add" if self._pane == "members" else "Tab Members"

    def _legend_shown(self) -> bool:
        """Whether a shown row carries the not-tracked mark."""
        wide = self._is_wide()
        members_shown = wide or self._pane == "members"
        add_shown = wide or self._pane == "add"
        return (members_shown and self._members_marked) or (
            add_shown and self._results_marked
        )

    # --- saving --------------------------------------------------------------

    def _save(self) -> None:
        """Ctrl+S: do what the draft's save plan asks.

        The overwrite confirm comes before the track question, so answering No
        to it never throws a tracking answer away.
        """
        self.draft.name = self._name_input.value
        plan = self.draft.save_plan()
        if plan.kind == DATA_NOT_LOADED:
            self._warn(_DATA_LOADING_WARNING)
            return
        if plan.kind == BAD_NAME:
            self._refuse_name(plan.message)
            return
        if plan.kind == SELF_NESTING:
            self._error(SELF_NESTING_MESSAGE)
            return
        if plan.replaces:
            self._confirm_overwrite(lambda: self._save_tracked(plan, overwrite=True))
        else:
            self._save_tracked(plan, overwrite=False)

    def _save_tracked(self, plan: SavePlan, *, overwrite: bool) -> None:
        """Ask the track question when the plan has untracked airports, then write."""
        if plan.kind == ASK_TRACK:
            self._ask_track(plan.untracked, overwrite=overwrite)
        else:
            self._write((), overwrite=overwrite)

    def _ask_track(self, untracked: tuple[str, ...], *, overwrite: bool) -> None:
        self.app.push_screen(
            ChoiceModal(track_offer_message(untracked), _TRACK_CHOICES),
            callback=lambda choice: self._on_track_choice(
                choice, untracked, overwrite=overwrite
            ),
        )

    def _on_track_choice(
        self, choice: str | None, untracked: tuple[str, ...], *, overwrite: bool
    ) -> None:
        """Add and save, or save only; Cancel and Esc write nothing."""
        if choice == "add":
            self._write(untracked, overwrite=overwrite)
        elif choice == "save":
            self._write((), overwrite=overwrite)

    def _write(self, track: tuple[str, ...], *, overwrite: bool = False) -> None:
        """Write the draft and close with the result, or show why it failed.

        Args:
            track: The airports the caller is to add to the tracked list.
            overwrite: Replace an entry already under the name.
        """
        try:
            saved = self.draft.write(overwrite=overwrite)
        except GroupingNameError as error:
            self._refuse_name(str(error))
            return
        except GroupingExistsError:
            # The file gained the name after the plan was made
            self._confirm_overwrite(lambda: self._write(track, overwrite=True))
            return
        except GroupingError as error:
            self._error(str(error))
            return
        result = BuilderResult(
            saved=saved, original_name=self.draft.original_name, track=track
        )
        self.dismiss(result)

    def _confirm_overwrite(self, on_replace: Callable[[], None]) -> None:
        """Ask to replace the grouping of that name; Yes runs `on_replace`."""

        def answered(replace: bool | None) -> None:
            if replace:
                on_replace()

        self.app.push_screen(ConfirmModal(self._overwrite_message()), callback=answered)

    def _overwrite_message(self) -> str:
        """The rename wording when editing under a new name, else the overwrite one."""
        name = self.draft.name.strip()
        original = self.draft.original_name
        if original is not None and name.lower() != original.lower():
            return RENAME_CONFIRM.format(new=name, old=original)
        return OVERWRITE_CONFIRM.format(name=name)

    def _refuse_name(self, message: str) -> None:
        """Show why the name was refused under the Name field, and focus it."""
        self._show_name_error(message)
        self._name_input.focus()

    def _show_name_error(self, message: str) -> None:
        """Show a message under the Name field; an empty one hides it."""
        error = self.query_one("#builder-name-error", Static)
        error.update(Content(message))
        error.display = bool(message)

    # --- cancelling ----------------------------------------------------------

    def action_cancel(self) -> None:
        """Esc: close at once when nothing changed, else ask first."""
        self.draft.name = self._name_input.value
        if not self.draft.changed:
            self.dismiss(None)
            return
        message = discard_message(self.draft.original_name)
        self.app.push_screen(ConfirmModal(message), callback=self._on_discard_answer)

    def _on_discard_answer(self, discard: bool | None) -> None:
        if discard:
            self.dismiss(None)

    # --- toasts --------------------------------------------------------------

    def _warn(self, message: str) -> None:
        """Explain a key press that did nothing because its precondition failed."""
        self.notify(message, severity="warning", markup=False)

    def _error(self, message: str) -> None:
        self.notify(message, severity="error", markup=False)
