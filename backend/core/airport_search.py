"""
Case-insensitive airport search over the unified airport data.

The index is built once from the unified data, so a query does not re-lower
every row, and it answers from the app's own data with no network call.
"""

import heapq
from dataclasses import dataclass
from typing import Any

# Fields whose value is an ident the query can match exactly.
_IDENT_FIELDS = ("icao", "faa", "iata")


@dataclass(frozen=True)
class AirportHit:
    """One airport search result.

    Attributes:
        ident: The row's canonical ident (the unified data's key).
        name: The airport's name.
        city: The airport's city.
        country: The airport's country.
        tracked: Whether the ident is in the caller's tracked set.
    """

    ident: str
    name: str
    city: str
    country: str
    tracked: bool


@dataclass(frozen=True)
class _Row:
    """A unified-data row with its search fields pre-lowered."""

    ident: str
    idents: tuple[str, ...]
    name: str
    city: str
    country: str
    name_lower: str
    city_lower: str

    def tier(self, needle: str) -> int | None:
        """Rank the row against a lower-cased needle.

        Args:
            needle: The query, stripped and lower-cased.

        Returns:
            0 for an exact ident, 1 for an ident prefix, 2 for a name or city
            substring, or None for no match.
        """
        if needle in self.idents:
            return 0
        if any(ident.startswith(needle) for ident in self.idents):
            return 1
        if needle in self.name_lower or needle in self.city_lower:
            return 2
        return None


def _text(value: Any) -> str:
    """Return a trimmed string for a unified-data field (empty when not a string)."""
    return value.strip() if isinstance(value, str) else ""


class AirportSearchIndex:
    """Match airports by ident, name or city, tracked ones first, then by tier."""

    def __init__(self, unified_data: dict[str, dict[str, Any]]) -> None:
        """Build the index from the unified airport data.

        Args:
            unified_data: Unified airport data keyed by ICAO (or FAA) code.
        """
        rows = []
        for ident, info in unified_data.items():
            fields = info if isinstance(info, dict) else {}
            idents = {ident.lower()}
            for field in _IDENT_FIELDS:
                value = _text(fields.get(field)).lower()
                if value:
                    idents.add(value)
            name = _text(fields.get("name"))
            city = _text(fields.get("city"))
            rows.append(
                _Row(
                    ident=ident,
                    idents=tuple(idents),
                    name=name,
                    city=city,
                    country=_text(fields.get("country")),
                    name_lower=name.lower(),
                    city_lower=city.lower(),
                )
            )
        self._rows: tuple[_Row, ...] = tuple(rows)

    def search(
        self, query: str, tracked: frozenset[str], limit: int
    ) -> list[AirportHit]:
        """Search the indexed airports.

        Args:
            query: The raw query; case-insensitive, stripped.
            tracked: The idents to rank ahead of the rest.
            limit: The most hits to return.

        Returns:
            Up to limit hits, tracked before untracked across every match tier,
            then by match tier, then by ident. An empty or blank query gives no
            hits.
        """
        return self.search_with_total(query, tracked, limit)[0]

    def search_with_total(
        self, query: str, tracked: frozenset[str], limit: int
    ) -> tuple[list[AirportHit], int]:
        """Search the indexed airports and count every match.

        Only the returned hits are built, so a short limit stays cheap however
        many rows match.

        Args:
            query: The raw query; case-insensitive, stripped.
            tracked: The idents to rank ahead of the rest.
            limit: The most hits to return.

        Returns:
            The hits `search` returns, and how many rows matched in all.
        """
        needle = query.strip().lower()
        if not needle:
            return [], 0

        ranked: list[tuple[bool, int, str, _Row]] = []
        for row in self._rows:
            tier = row.tier(needle)
            if tier is None:
                continue
            ranked.append((row.ident not in tracked, tier, row.ident, row))
        best = heapq.nsmallest(
            limit, ranked, key=lambda item: (item[0], item[1], item[2])
        )

        hits = [
            AirportHit(
                ident=row.ident,
                name=row.name,
                city=row.city,
                country=row.country,
                tracked=row.ident in tracked,
            )
            for _, _, _, row in best
        ]
        return hits, len(ranked)
