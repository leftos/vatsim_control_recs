"""The grouping builder's draft model and the wording the screen shows.

Pure Python: this module holds the screen's state, decides what a save must ask
first and writes through `backend.core.groupings`, but imports no Textual, so
the tests drive it without an app.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from backend.core.airport_search import AirportSearchIndex
from backend.core.groupings import (
    UserGrouping,
    check_grouping_name,
    rename_user_grouping,
    save_user_grouping,
)

# The most ICAOs the track offer spells out before ", +N".
_MAX_OFFERED_ICAOS = 6

# The confirm wording the builder shows, as the owner settled it.
OVERWRITE_CONFIRM = "'{name}' is already one of my groupings. Replace it?"
RENAME_CONFIRM = (
    "'{new}' is already one of my groupings. Replace it with this one? "
    "('{old}' is removed)"
)
DISCARD_CONFIRM = "Discard your changes to '{name}'?"
DISCARD_BLANK_CONFIRM = "Discard your changes to this grouping?"
TRACK_OFFER = (
    "{n} of these aren't tracked ({icaos}). Add them to your tracked airports?"
)
TRACK_ADD_LABEL = "Add and save"
TRACK_SAVE_ONLY_LABEL = "Save only"
TRACK_CANCEL_LABEL = "Cancel"

# The kinds of save plan a draft reports.
DATA_NOT_LOADED = "data-not-loaded"
SELF_NESTING = "self-nesting"
ASK_TRACK = "ask-track"
WRITE = "write"

# What the screen shows when the name would nest one of the draft's members.
SELF_NESTING_MESSAGE = "a grouping can't contain itself"


class DraftError(ValueError):
    """The draft cannot make the asked change.

    The message is user-facing: the screen shows it, as it shows a
    `GroupingError`'s.
    """


@dataclass(frozen=True)
class BuilderContext:
    """Everything the draft reads but does not own.

    Attributes:
        groupings: Every grouping name to its member refs, as
            `vatsim_app.cached_groupings` holds them.
        user_names: The names of the user's own (★) groupings.
        tracked: The tracked airports; empty means every airport is tracked.
        search_index: The airport search index, or None while the unified airport
            data has not been loaded yet.
        preset_names: The grouping names owned by a preset source.
        airport_idents: Every airport ident the unified data knows: the keys plus
            each row's ICAO, FAA and IATA fields, so an IATA-only ref cannot be
            told apart from the rest and resolves as it is written.
    """

    groupings: Mapping[str, Sequence[str]]
    user_names: frozenset[str]
    tracked: frozenset[str]
    search_index: AirportSearchIndex | None
    preset_names: frozenset[str]
    airport_idents: frozenset[str]


@dataclass(frozen=True)
class SavePlan:
    """What the screen must do when the user saves, without doing its I/O.

    Attributes:
        kind: One of `DATA_NOT_LOADED` (warn and wait), `SELF_NESTING` (refuse the
            name and show `SELF_NESTING_MESSAGE`), `ASK_TRACK` (ask the three-way
            tracking question with `untracked`) or `WRITE` (save now).
        untracked: The resolved airports the tracked list lacks, for `ASK_TRACK`.
        item: The member whose name would nest the draft's own, for `SELF_NESTING`.
    """

    kind: str
    untracked: tuple[str, ...] = ()
    item: str | None = None


@dataclass(frozen=True)
class BuilderResult:
    """What the builder screen dismisses with, for its caller to apply.

    Attributes:
        saved: The grouping as it was written.
        original_name: The name being edited, or None for a new grouping.
        track: The airports the user chose to add to the tracked list; empty for
            "Save only".
    """

    saved: UserGrouping
    original_name: str | None
    track: tuple[str, ...]


def discard_message(name: str, original_name: str | None = None) -> str:
    """The confirm text for discarding the draft's changes.

    Args:
        name: The name in the Name field, which may be blank or padded.
        original_name: The name being edited, used when the field is blank.

    Returns:
        The wording for a named grouping, the edited grouping's name when the field
        is blank but one is being edited, or the "this grouping" wording when both
        are blank.
    """
    for candidate in (name, original_name):
        if candidate and candidate.strip():
            return DISCARD_CONFIRM.format(name=candidate.strip())
    return DISCARD_BLANK_CONFIRM


def track_offer_message(icaos: Sequence[str]) -> str:
    """The three-way tracking question for the airports the draft leaves untracked.

    Args:
        icaos: The untracked airports, in the order the draft resolved them.

    Returns:
        The wording, spelling out at most six ICAOs and then ", +N".
    """
    listed = ", ".join(icaos[:_MAX_OFFERED_ICAOS])
    rest = len(icaos) - _MAX_OFFERED_ICAOS
    if rest > 0:
        listed = f"{listed}, +{rest}"
    return TRACK_OFFER.format(n=len(icaos), icaos=listed)


class GroupingDraft:
    """The builder screen's model: its member refs, their filters and its name.

    The draft owns no file and no widget: `write` is the only method that touches
    the backend, and `save_plan` says what the screen must ask before calling it.
    """

    def __init__(
        self,
        context: BuilderContext,
        *,
        items: Sequence[str],
        filters: Mapping[str, str],
        name: str,
        original_name: str | None,
    ) -> None:
        """Start a draft over a context.

        Args:
            context: What the draft reads but does not own.
            items: The member refs in order (airport idents and grouping names);
                a repeated ref is kept once, at its first position.
            filters: Airport ident to "dep" or "arr".
            name: The name in the Name field.
            original_name: The ★ grouping being edited, or None for a new one.
        """
        self.context = context
        self.items = list(dict.fromkeys(items))
        self.filters = dict(filters)
        self.name = name
        self.original_name = original_name
        self._initial = (tuple(self.items), dict(self.filters), self.name.strip())

    @property
    def changed(self) -> bool:
        """Whether the draft differs from the state the screen opened in."""
        items, filters, name = self._initial
        return (
            tuple(self.items) != items
            or self.filters != filters
            or self.name.strip() != name
        )

    def add(self, ref: str) -> None:
        """Append a member ref.

        Args:
            ref: The airport ident or grouping name to append.

        Raises:
            DraftError: The ref is already a member, or it is this grouping under
                its own name or the name being edited from, directly or nested.
        """
        if ref in self.items:
            raise DraftError(f"'{ref}' is already in this grouping")
        if self._nests_self(ref):
            raise DraftError(f"'{ref}' can't be added: a grouping can't contain itself")
        self.items.append(ref)

    def remove(self, ref: str) -> None:
        """Drop a direct member.

        Args:
            ref: The member to drop.

        Raises:
            DraftError: The ref is not a member of this grouping.
        """
        if ref not in self.items:
            raise DraftError(f"'{ref}' is not in this grouping")
        self.items.remove(ref)

    def exclude(self, group_ref: str, icao: str) -> None:
        """Replace a nested grouping member with its airports, less one of them.

        The airports take the ref's position, in the grouping's own order, and any
        the other members already resolve to are skipped. Filters carry over
        unchanged; a filter for an airport that is no longer resolved is dropped
        when the draft is saved.

        Args:
            group_ref: The nested grouping member to expand.
            icao: The airport to leave out of the expansion.

        Raises:
            DraftError: The ref is not a member, is not a grouping, or the airport
                is not one of the grouping's resolved airports.
        """
        if group_ref not in self.items:
            raise DraftError(f"'{group_ref}' is not in this grouping")
        if group_ref not in self.context.groupings:
            raise DraftError("only a grouping's members can be excluded")
        members = self.airports_of(group_ref)
        if icao not in members:
            raise DraftError(f"'{icao}' is not in '{group_ref}'")

        others = self._airports_of_the_other_items(group_ref)
        replacement = [
            member for member in members if member != icao and member not in others
        ]
        expanded: list[str] = []
        for item in self.items:
            if item == group_ref:
                expanded.extend(replacement)
            else:
                expanded.append(item)
        self.items = expanded

    def cycle_filter(self, icao: str) -> str | None:
        """Cycle one airport's filter both → dep → arr → both.

        Args:
            icao: The airport whose filter changes.

        Returns:
            The new filter, or None for both (which stores no key).
        """
        current = self.filters.get(icao)
        if current == "dep":
            following: str | None = "arr"
        elif current == "arr":
            following = None
        else:
            following = "dep"
        if following is None:
            self.filters.pop(icao, None)
        else:
            self.filters[icao] = following
        return following

    def resolved_airports(self) -> list[str]:
        """Every airport the members resolve to, in order, without duplicates.

        An unknown ref (neither a grouping name nor a known airport ident)
        contributes nothing and stays a member.
        """
        airports: list[str] = []
        seen_names: set[str] = set()
        seen_airports: set[str] = set()
        for item in self.items:
            self._walk(item, seen_names, seen_airports, airports)
        return airports

    def untracked(self) -> list[str]:
        """The resolved airports the tracked list lacks.

        Empty when the tracked list is empty, since an empty list tracks every
        airport.
        """
        if not self.context.tracked:
            return []
        return [
            icao
            for icao in self.resolved_airports()
            if icao not in self.context.tracked
        ]

    def is_tracked(self, icao: str) -> bool:
        """Whether the tracked list covers an airport, which an empty list does."""
        return not self.context.tracked or icao in self.context.tracked

    def to_grouping(self) -> UserGrouping:
        """The grouping to write: the stripped name, the members and their filters.

        Filters of airports the members no longer resolve to are dropped; a
        nested member's filter is kept because that airport is resolved.
        """
        resolved = set(self.resolved_airports())
        return UserGrouping(
            name=self.name.strip(),
            items=tuple(self.items),
            filters={
                icao: value for icao, value in self.filters.items() if icao in resolved
            },
        )

    def check_name(self) -> None:
        """Apply the backend's name rules ahead of a write, as the write would.

        The rules skip a name that matches, case-insensitively, the name being
        edited or one of my groupings: the write then renames that entry or
        asks to overwrite it.

        Raises:
            GroupingNameError: The name is blank, a preset's, or an airport ident.
        """
        name = self.name.strip()
        stored = {*self.context.user_names, self.original_name or ""}
        if name and any(name.lower() == other.lower() for other in stored if other):
            return
        check_grouping_name(
            name,
            preset_names=self.context.preset_names,
            airport_idents=self.context.airport_idents,
        )

    def save_plan(self) -> SavePlan:
        """What the screen must do next when the user saves."""
        if self.context.search_index is None:
            return SavePlan(kind=DATA_NOT_LOADED)
        for item in self.items:
            if self._nests_self(item):
                return SavePlan(kind=SELF_NESTING, item=item)
        untracked = self.untracked()
        if untracked:
            return SavePlan(kind=ASK_TRACK, untracked=tuple(untracked))
        return SavePlan(kind=WRITE)

    def write(self, *, overwrite: bool) -> UserGrouping:
        """Write the draft through the backend and return what was written.

        Editing always renames, even when the name is unchanged or differs only in
        case, which the backend handles as one entry.

        The screen calls `save_plan` first: writing before the airport data is
        loaded would pass an empty `airport_idents` and drop every filter.

        Args:
            overwrite: Replace an entry already under the new name.

        Returns:
            The grouping as it was written.

        Raises:
            GroupingError: The name is blank, a preset's or an airport ident, an
                entry of that name exists and overwrite is false, the name being
                edited is gone, or the file cannot be read or written.
        """
        grouping = self.to_grouping()
        if self.original_name is not None:
            rename_user_grouping(
                self.original_name,
                grouping,
                overwrite=overwrite,
                preset_names=self.context.preset_names,
                airport_idents=self.context.airport_idents,
            )
        else:
            save_user_grouping(
                grouping,
                overwrite=overwrite,
                preset_names=self.context.preset_names,
                airport_idents=self.context.airport_idents,
            )
        return grouping

    def _nests_self(self, ref: str) -> bool:
        """Whether a ref is this grouping, or a grouping that contains it."""
        names = {
            value.strip().lower()
            for value in (self.name, self.original_name or "")
            if value.strip()
        }
        if not names:
            return False

        seen: set[str] = set()
        pending = [ref]
        while pending:
            current = pending.pop()
            if current.lower() in names:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(self.context.groupings.get(current, ()))
        return False

    def _airports_of_the_other_items(self, group_ref: str) -> set[str]:
        """Every airport the members other than `group_ref` resolve to."""
        others: set[str] = set()
        for item in self.items:
            if item != group_ref:
                others.update(self.airports_of(item))
        return others

    def airports_of(self, ref: str) -> list[str]:
        """The airports one ref resolves to, in order, without duplicates.

        A grouping name gives its airports in the grouping's own order; an
        airport ident gives itself when the unified data knows it.
        """
        airports: list[str] = []
        self._walk(ref, set(), set(), airports)
        return airports

    def _walk(
        self,
        ref: str,
        seen_names: set[str],
        seen_airports: set[str],
        airports: list[str],
    ) -> None:
        """Append a ref's airports to `airports`, depth first and once each.

        A ref that names a grouping is walked member by member; any other ref is
        an airport of its own, kept as its normalised ident only when the unified
        data knows that ident.
        """
        if ref in seen_names:
            return
        seen_names.add(ref)

        members = self.context.groupings.get(ref)
        if members is None:
            ident = ref.strip().upper()
            if ident in self.context.airport_idents and ident not in seen_airports:
                seen_airports.add(ident)
                airports.append(ident)
            return

        for member in members:
            self._walk(member, seen_names, seen_airports, airports)
