"""
Unified airport data loader that merges data from multiple sources:
- APT_BASE.csv (FAA data with ARTCC info)
- airports.json (general airport information)
- iata-icao.csv (coordinate data)

Priority order for merging: APT_BASE.csv > airports.json > iata-icao.csv
Primary key: ICAO code if available, otherwise FAA/IATA code
Conflict resolution: Prefer US/USA airports, otherwise first encountered
"""

import csv
import json
import os
from typing import Dict, Any, Optional

def load_unified_airport_data(
    apt_base_path: str,
    airports_json_path: str,
    iata_icao_path: str
) -> Dict[str, Dict[str, Any]]:
    """
    Load and merge airport data from all three sources.
    
    Note: Caching should be handled by the caller if needed.
    
    Returns a dictionary mapping airport codes (ICAO preferred, FAA otherwise) to airport info:
    {
        'KJFK': {
            'icao': 'KJFK',
            'iata': 'JFK',
            'faa': 'JFK',
            'name': 'John F Kennedy International Airport',
            'city': 'New York',
            'state': 'New York',
            'country': 'US',
            'latitude': 40.6398,
            'longitude': -73.7789,
            'elevation': 13,
            'artcc': 'ZNY',
            'tz': 'America/New_York'
        }
    }
    """
    #print("Loading unified airport data...")
    airports = {}
    
    # Step 1: Load iata-icao.csv (lowest priority - base coordinates)
    try:
        with open(iata_icao_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                icao = row.get('icao', '').strip()
                if not icao:
                    continue
                
                airports[icao] = {
                    'icao': icao,
                    'iata': row.get('iata', '').strip(),
                    'faa': icao,  # Will be updated by APT_BASE if available
                    'name': row.get('name', '').strip(),
                    'city': row.get('city', '').strip(),
                    'state': row.get('state', '').strip(),
                    'country': row.get('country_code', row.get('country', '')).strip(),
                    'latitude': float(row['latitude']) if row.get('latitude') else None,
                    'longitude': float(row['longitude']) if row.get('longitude') else None,
                    'elevation': int(row['elevation']) if row.get('elevation') else None,
                    'artcc': '',
                    'tz': row.get('tz', '').strip(),
                    'tower_type': ''  # Will be updated by APT_BASE if available
                }
        #print(f"  Loaded {len(airports)} airports from iata-icao.csv")
    except FileNotFoundError:
        print(f"  Warning: {iata_icao_path} not found")
    except Exception as e:
        print(f"  Warning: Error loading iata-icao.csv: {e}")
    
    # Step 2: Load airports.json (medium priority - additional details)
    try:
        with open(airports_json_path, 'r', encoding='utf-8') as f:
            airports_json_data = json.load(f)
            
            for code, details in airports_json_data.items():
                icao = (details.get('icao') or code or '').strip()
                if not icao:
                    continue
                
                # Helper function to safely get and strip string values
                def safe_strip(value, default=''):
                    return (value or default).strip() if isinstance(value, str) or value else default
                
                # If airport already exists, merge data (airports.json takes priority for these fields)
                if icao in airports:
                    airports[icao].update({
                        'name': safe_strip(details.get('name'), airports[icao]['name']),
                        'city': safe_strip(details.get('city'), airports[icao]['city']),
                        'state': safe_strip(details.get('state'), airports[icao]['state']),
                        'country': safe_strip(details.get('country'), airports[icao]['country']),
                        'latitude': details.get('lat', airports[icao]['latitude']),
                        'longitude': details.get('lon', airports[icao]['longitude']),
                        'elevation': details.get('elevation', airports[icao]['elevation']),
                        'tz': safe_strip(details.get('tz'), airports[icao]['tz']),
                    })
                else:
                    # New airport from airports.json
                    airports[icao] = {
                        'icao': icao,
                        'iata': safe_strip(details.get('iata')),
                        'faa': icao,
                        'name': safe_strip(details.get('name')),
                        'city': safe_strip(details.get('city')),
                        'state': safe_strip(details.get('state')),
                        'country': safe_strip(details.get('country')),
                        'latitude': details.get('lat'),
                        'longitude': details.get('lon'),
                        'elevation': details.get('elevation'),
                        'artcc': '',
                        'tz': safe_strip(details.get('tz')),
                        'tower_type': ''  # Will be updated by APT_BASE if available
                    }
        #print(f"  Merged data from airports.json (now {len(airports)} airports)")
    except FileNotFoundError:
        print(f"  Warning: {airports_json_path} not found")
    except Exception as e:
        print(f"  Warning: Error loading airports.json: {e}")
    
    # Step 3: Load APT_BASE.csv (highest priority - FAA official data with ARTCC)
    duplicate_codes = {}  # Track duplicate codes for conflict resolution
    
    try:
        with open(apt_base_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                icao_id = row.get('ICAO_ID', '').strip()
                arpt_id = row.get('ARPT_ID', '').strip()
                
                # Determine primary code: ICAO if available, otherwise FAA
                primary_code = icao_id if icao_id else arpt_id
                if not primary_code:
                    continue
                
                # Parse coordinates from APT_BASE format
                try:
                    lat_decimal = float(row.get('LAT_DECIMAL', '')) if row.get('LAT_DECIMAL') else None
                    long_decimal = float(row.get('LONG_DECIMAL', '')) if row.get('LONG_DECIMAL') else None
                    elevation = int(row.get('ELEV', '')) if row.get('ELEV') else None
                except (ValueError, TypeError):
                    lat_decimal = None
                    long_decimal = None
                    elevation = None
                
                country = row.get('COUNTRY_CODE', '').strip()
                
                # Handle duplicate codes
                if primary_code in airports:
                    # Check if we should replace the existing entry
                    existing_country = airports[primary_code].get('country', '')
                    
                    # If both are US or both are non-US, just update the existing entry with APT_BASE data
                    # (APT_BASE has the highest priority for ARTCC and other FAA data)
                    if (country in ('US', 'USA') and existing_country in ('US', 'USA')) or \
                       (country not in ('US', 'USA') and existing_country not in ('US', 'USA')):
                        # Both same country preference - update with APT_BASE data but keep existing info
                        pass
                    elif country in ('US', 'USA') and existing_country not in ('US', 'USA'):
                        # New one is US, existing is not - replace with US airport
                        pass
                    elif existing_country in ('US', 'USA') and country not in ('US', 'USA'):
                        # Existing is US, new one is not - keep existing, skip this one
                        if primary_code not in duplicate_codes:
                            duplicate_codes[primary_code] = []
                        duplicate_codes[primary_code].append(row)
                        continue
                
                # Create or update airport entry
                airport_data = airports.get(primary_code, {
                    'icao': '',
                    'iata': '',
                    'faa': '',
                    'name': '',
                    'city': '',
                    'state': '',
                    'country': '',
                    'latitude': None,
                    'longitude': None,
                    'elevation': None,
                    'artcc': '',
                    'tz': '',
                    'tower_type': ''
                })
                
                # Update with APT_BASE data (highest priority)
                airport_data.update({
                    'icao': icao_id if icao_id else airport_data.get('icao', ''),
                    'faa': arpt_id,
                    'name': row.get('ARPT_NAME', airport_data.get('name', '')).strip(),
                    'city': row.get('CITY', airport_data.get('city', '')).strip(),
                    'state': row.get('STATE_NAME', airport_data.get('state', '')).strip(),
                    'country': country or airport_data.get('country', ''),
                    'latitude': lat_decimal if lat_decimal is not None else airport_data.get('latitude'),
                    'longitude': long_decimal if long_decimal is not None else airport_data.get('longitude'),
                    'elevation': elevation if elevation is not None else airport_data.get('elevation'),
                    'artcc': row.get('RESP_ARTCC_ID', '').strip(),
                    'tower_type': row.get('TWR_TYPE_CODE', '').strip(),
                })
                
                airports[primary_code] = airport_data
        
        #print(f"  Merged data from APT_BASE.csv (now {len(airports)} airports)")
        if duplicate_codes:
            #print(f"  Resolved {len(duplicate_codes)} duplicate codes (preferred US airports)")
            pass
    except FileNotFoundError:
        print(f"  Warning: {apt_base_path} not found")
    except Exception as e:
        print(f"  Warning: Error loading APT_BASE.csv: {e}")
    
    #print(f"✓ Unified airport data loaded: {len(airports)} airports\n")
    return airports