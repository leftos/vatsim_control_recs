"""Data management for airport information."""

import json
from collections import defaultdict
from typing import Any, Dict, Optional

from common import logger


class AirportDataManager:
    """Manages airport data loading, caching, and location mapping."""

    def __init__(
        self,
        airports_file_path: str,
        unified_data: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        """
        Initialize the data manager.

        Args:
            airports_file_path: Path to airports.json file
            unified_data: Optional pre-loaded unified airport data
        """
        self.airports_file_path = airports_file_path

        # Load airports data - prefer unified_data if provided
        if unified_data:
            self.airports_data = self._convert_unified_data(unified_data)
        else:
            self.airports_data = self._load_from_file()

        # Build location mappings
        self.location_to_airports = defaultdict(list)
        self.icao_to_location = {}
        self._build_location_mappings()

    def _convert_unified_data(
        self, unified_data: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """Convert unified data format to airports.json format for compatibility."""
        airports_data = {}
        for code, info in unified_data.items():
            airports_data[code] = {
                "icao": info.get("icao", code),
                "iata": info.get("iata", ""),
                "name": info.get("name", ""),
                "city": info.get("city", ""),
                "state": info.get("state", ""),
                "country": info.get("country", ""),
                "lat": info.get("latitude"),
                "lon": info.get("longitude"),
                "elevation": info.get("elevation"),
                "tz": info.get("tz", ""),
            }
        return airports_data

    def _load_from_file(self) -> Dict[str, Dict[str, Any]]:
        """Load airport data from JSON file."""
        try:
            with open(self.airports_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                logger.info(
                    f"Loaded {len(data)} airports from {self.airports_file_path}"
                )
                return data
        except FileNotFoundError:
            logger.error(f"Airport data file not found: {self.airports_file_path}")
            return {}
        except json.JSONDecodeError as e:
            logger.error(
                f"Invalid JSON in airport data file: {self.airports_file_path}: {e}"
            )
            return {}

    def is_loaded(self) -> bool:
        """Check if airport data was successfully loaded."""
        return bool(self.airports_data)

    def _build_location_mappings(self):
        """Build mappings between airports and their base locations."""
        for icao, details in self.airports_data.items():
            base_location = self._get_base_location(details)
            if base_location:
                self.location_to_airports[base_location].append(icao)
                self.icao_to_location[icao] = base_location

    def _get_base_location(self, airport_details: Dict[str, Any]) -> str:
        """
        Determine whether to use state or city as the base location name.
        Prefers state if the airport name starts with it, otherwise uses city.
        """
        name = airport_details.get("name", "")
        state = airport_details.get("state", "")
        city = airport_details.get("city", "")

        # Prefer state if the airport name starts with it
        if state and name.lower().startswith(state.lower()):
            return state
        return city

    def get_airport_details(self, icao: str) -> Optional[Dict[str, Any]]:
        """Get details for a specific airport."""
        return self.airports_data.get(icao)

    def get_airports_in_location(self, location: str) -> list:
        """Get all airport ICAOs for a given location."""
        return self.location_to_airports.get(location, [])

    def get_location_for_airport(self, icao: str) -> Optional[str]:
        """Get the base location for an airport."""
        return self.icao_to_location.get(icao)
