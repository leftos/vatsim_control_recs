"""Main airport disambiguator class providing the public API."""

import sys
import time
from typing import Any, Dict, Optional

from .config import DEFAULT_CONFIG, DisambiguatorConfig
from .data_manager import AirportDataManager
from .disambiguation_engine import DisambiguationEngine
from .entity_extractor import EntityExtractor
from .name_processor import NameProcessor


class AirportDisambiguator:
    """
    Disambiguates airport names to create unique, readable identifiers.
    
    This is the main public interface that maintains backwards compatibility
    with the original implementation while using a cleaner internal architecture.
    """
    
    def __init__(self, airports_file_path: str, lazy_load: bool = True, 
                 unified_data: Optional[Dict[str, Dict[str, Any]]] = None,
                 config: Optional[DisambiguatorConfig] = None):
        """
        Initialize the airport disambiguator.
        
        Args:
            airports_file_path: Path to airports.json file
            lazy_load: If True, process locations on-demand. If False, process all upfront.
            unified_data: Optional pre-loaded unified airport data
            config: Optional configuration object (uses default if not provided)
        """
        self.airports_file_path = airports_file_path
        self.lazy_load = lazy_load
        self.config = config or DEFAULT_CONFIG
        
        # Initialize components
        self.data_manager = AirportDataManager(airports_file_path, unified_data)
        self.name_processor = NameProcessor(self.config)
        self.entity_extractor = EntityExtractor(self.config)
        self.disambiguation_engine = DisambiguationEngine(
            self.config, self.name_processor, self.entity_extractor
        )
        
        # Cache for pretty names (abbreviated to max length)
        self.icao_to_pretty_name = {}
        # Cache for full names (no length limit)
        self.icao_to_full_name = {}
        self._processed_locations = set()
        
        # Process all airports upfront if not lazy loading
        if not lazy_load:
            self._eager_load_all()
    
    def _eager_load_all(self):
        """Process all airports upfront (eager loading mode)."""
        start_time = time.time()
        print("Generating airport disambiguation mappings...")
        sys.stdout.flush()
        
        gen_start = time.time()
        self._generate_all_pretty_names()
        gen_time = time.time() - gen_start
        
        total_time = time.time() - start_time
        print(f"✓ Processed {len(self.icao_to_pretty_name)} airports in {gen_time:.2f}s")
        print(f"✓ Disambiguator ready! (total: {total_time:.2f}s)\n")
        sys.stdout.flush()
    
    def _generate_all_pretty_names(self):
        """Generate pretty names for all airports."""
        total_locations = len(self.data_manager.location_to_airports)
        processed = 0
        last_progress = 0
        
        print(f"  Processing airports... 0% ({processed}/{total_locations} locations)")
        
        for location, icaos in self.data_manager.location_to_airports.items():
            processed += 1
            
            # Update progress every 10%
            progress_pct = int((processed / total_locations) * 100)
            if progress_pct >= last_progress + 10:
                print(f"  Processing airports... {progress_pct}% ({processed}/{total_locations} locations)")
                sys.stdout.flush()
                last_progress = progress_pct
            
            if not icaos:
                continue
            
            # Process this location
            self._process_location_internal(location, icaos)
    
    def _process_location_internal(self, location: str, icaos: list):
        """Internal method to process a specific location's airports."""
        if len(icaos) == 1:
            # Single airport in this location
            icao = icaos[0]
            airport_details = self.data_manager.get_airport_details(icao)
            if airport_details:
                full_name = self.disambiguation_engine.disambiguate_single_airport(
                    icao, airport_details, location
                )
                # Store full name before abbreviation
                self.icao_to_full_name[icao] = full_name
                # Apply abbreviation for long names
                pretty_name = self.name_processor.abbreviate_long_name(full_name)
                self.icao_to_pretty_name[icao] = pretty_name
        else:
            # Multiple airports in this location
            disambiguated = self.disambiguation_engine.disambiguate_multiple_airports(
                icaos, self.data_manager.airports_data, location
            )
            # Store full names before abbreviation
            self.icao_to_full_name.update(disambiguated)
            # Apply abbreviation for long names
            for icao, name in disambiguated.items():
                disambiguated[icao] = self.name_processor.abbreviate_long_name(name)
            self.icao_to_pretty_name.update(disambiguated)
    
    def _process_location(self, location: str):
        """Process all airports in a specific location on-demand (lazy loading)."""
        if location in self._processed_locations:
            return  # Already processed
        
        self._processed_locations.add(location)
        icaos = self.data_manager.get_airports_in_location(location)
        
        if icaos:
            self._process_location_internal(location, icaos)
    
    def get_pretty_name(self, icao: str) -> str:
        """
        Get the pretty name for an airport.
        
        Args:
            icao: The ICAO code of the airport
            
        Returns:
            The pretty/disambiguated name, or the ICAO code if not found
        """
        # If lazy loading is enabled and this location hasn't been processed yet
        if self.lazy_load:
            location = self.data_manager.get_location_for_airport(icao)
            if location and location not in self._processed_locations:
                self._process_location(location)
        
        return self.icao_to_pretty_name.get(icao, icao)
    
    def get_pretty_names_batch(self, icaos: list[str]) -> dict[str, str]:
        """
        Get pretty names for multiple airports efficiently.
        
        This method processes all unique locations at once, which is much more efficient
        than calling get_pretty_name() individually for many airports in the same location.
        
        Args:
            icaos: List of ICAO codes to get pretty names for
            
        Returns:
            Dictionary mapping ICAO codes to pretty names
        """
        # If not lazy loading, all names are already computed
        if not self.lazy_load:
            return {icao: self.icao_to_pretty_name.get(icao, icao) for icao in icaos}
        
        # For lazy loading, find all unprocessed locations
        locations_to_process = set()
        for icao in icaos:
            location = self.data_manager.get_location_for_airport(icao)
            if location and location not in self._processed_locations:
                locations_to_process.add(location)
        
        # Process all locations at once
        for location in locations_to_process:
            self._process_location(location)
        
        # Return the pretty names
        return {icao: self.icao_to_pretty_name.get(icao, icao) for icao in icaos}

    def get_full_name(self, icao: str) -> str:
        """
        Get the full disambiguated name for an airport (without length limit).

        Args:
            icao: The ICAO code of the airport

        Returns:
            The full disambiguated name, or the ICAO code if not found
        """
        # If lazy loading is enabled and this location hasn't been processed yet
        if self.lazy_load:
            location = self.data_manager.get_location_for_airport(icao)
            if location and location not in self._processed_locations:
                self._process_location(location)

        return self.icao_to_full_name.get(icao, icao)

    def get_full_names_batch(self, icaos: list[str]) -> dict[str, str]:
        """
        Get full names for multiple airports efficiently (without length limit).

        This method processes all unique locations at once, which is much more efficient
        than calling get_full_name() individually for many airports in the same location.

        Args:
            icaos: List of ICAO codes to get full names for

        Returns:
            Dictionary mapping ICAO codes to full names
        """
        # If not lazy loading, all names are already computed
        if not self.lazy_load:
            return {icao: self.icao_to_full_name.get(icao, icao) for icao in icaos}

        # For lazy loading, find all unprocessed locations
        locations_to_process = set()
        for icao in icaos:
            location = self.data_manager.get_location_for_airport(icao)
            if location and location not in self._processed_locations:
                locations_to_process.add(location)

        # Process all locations at once
        for location in locations_to_process:
            self._process_location(location)

        # Return the full names
        return {icao: self.icao_to_full_name.get(icao, icao) for icao in icaos}

    # Properties for backwards compatibility with original implementation
    @property
    def nlp(self):
        """Access the spaCy NLP model (for backwards compatibility)."""
        return self.entity_extractor.nlp
    
    @property
    def airports_data(self):
        """Access the airports data (for backwards compatibility)."""
        return self.data_manager.airports_data
    
    @property
    def location_to_airports(self):
        """Access the location to airports mapping (for backwards compatibility)."""
        return self.data_manager.location_to_airports
    
    @property
    def icao_to_location(self):
        """Access the ICAO to location mapping (for backwards compatibility)."""
        return self.data_manager.icao_to_location