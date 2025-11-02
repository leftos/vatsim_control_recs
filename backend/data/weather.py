"""
Weather data fetching for airports (METAR and wind information).
"""

import json
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Tuple

from backend.cache.manager import get_wind_cache, get_metar_cache
from backend.config.constants import WIND_CACHE_DURATION, METAR_CACHE_DURATION


def _parse_wind_from_observation(properties: dict) -> Tuple[bool, str]:
    """
    Parse wind data from a single observation.
    
    Args:
        properties: Observation properties dictionary from weather.gov API
    
    Returns:
        (has_data, wind_string) tuple where has_data is True if valid wind data was found
    """
    wind_direction = properties.get('windDirection', {}).get('value')
    wind_speed_kmh = properties.get('windSpeed', {}).get('value')
    wind_gust_kmh = properties.get('windGust', {}).get('value')
    
    # Check if we have valid wind data
    if wind_direction is None or wind_speed_kmh is None:
        return (False, "")
    
    # Convert km/h to knots (1 knot = 1.852 km/h)
    wind_speed_knots = round(wind_speed_kmh / 1.852)
    
    # Handle calm winds (0 knots)
    if wind_speed_knots == 0:
        return (True, "00000KT")
    
    # Format base wind: "27005KT"
    wind_str = f"{int(wind_direction):03d}{wind_speed_knots:02d}"
    
    # Add gusts if present and greater than steady wind
    if wind_gust_kmh is not None and wind_gust_kmh > 0:
        wind_gust_knots = round(wind_gust_kmh / 1.852)
        if wind_gust_knots > wind_speed_knots:
            wind_str += f"G{wind_gust_knots:02d}"
    
    # Add KT suffix
    wind_str += "KT"
    
    return (True, wind_str)


def get_wind_info_minute(airport_icao: str) -> str:
    """
    Fetch current wind information from weather.gov API with caching.
    
    Wind data is cached for 60 seconds to avoid excessive API calls.
    If the latest observation doesn't have wind data, fetches the last 10 observations
    and returns the most recent one with valid wind data.
    Airports returning 404 are blacklisted and never queried again.
    
    Args:
        airport_icao: The ICAO code of the airport
        
    Returns:
        Formatted wind string like "27005G12KT" or "27005KT" or empty string if unavailable
    """
    wind_data_cache, wind_blacklist = get_wind_cache()
    
    # Check if airport is blacklisted (doesn't have weather data available)
    if airport_icao in wind_blacklist:
        return ""
    
    # Check if we have valid cached data
    current_time = datetime.now(timezone.utc)
    if airport_icao in wind_data_cache:
        cache_entry = wind_data_cache[airport_icao]
        time_since_cache = (current_time - cache_entry['timestamp']).total_seconds()
        if time_since_cache < WIND_CACHE_DURATION:
            return cache_entry['wind_info']
    
    # Cache miss or expired - fetch new data
    try:
        # First, try the latest observation
        url = f"https://api.weather.gov/stations/{airport_icao}/observations/latest"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'VATSIM-Control-Recs/1.0')
        
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode())
        
        properties = data.get('properties', {})
        has_data, wind_str = _parse_wind_from_observation(properties)
        
        # If latest observation doesn't have wind data, try the last 10 observations
        if not has_data:
            url = f"https://api.weather.gov/stations/{airport_icao}/observations?limit=10"
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'VATSIM-Control-Recs/1.0')
            
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode())
            
            # Iterate through observations to find the first one with wind data
            features = data.get('features', [])
            for feature in features:
                properties = feature.get('properties', {})
                has_data, wind_str = _parse_wind_from_observation(properties)
                if has_data:
                    break
        
        # Cache the result (even if empty)
        wind_data_cache[airport_icao] = {
            'wind_info': wind_str,
            'timestamp': current_time
        }
        
        return wind_str
        
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Station doesn't exist - blacklist it permanently
            wind_blacklist.add(airport_icao)
            return ""
        # For other HTTP errors, return cached data if available
        if airport_icao in wind_data_cache:
            return wind_data_cache[airport_icao]['wind_info']
        return ""
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError, TimeoutError):
        # On other errors, return cached data if available (even if expired), otherwise empty string
        if airport_icao in wind_data_cache:
            return wind_data_cache[airport_icao]['wind_info']
        return ""


def get_metar(airport_icao: str) -> str:
    """
    Fetch current METAR from aviationweather.gov API with caching.
    
    METAR data is cached for 60 seconds to avoid excessive API calls.
    Airports returning 404 or no data are blacklisted and never queried again in this session.
    
    Args:
        airport_icao: The ICAO code of the airport
        
    Returns:
        Full METAR string or empty string if unavailable
    """
    metar_data_cache, metar_blacklist = get_metar_cache()
    
    # Check if airport is blacklisted (doesn't have METAR data available)
    if airport_icao in metar_blacklist:
        return ""
    
    # Check if we have valid cached data
    current_time = datetime.now(timezone.utc)
    if airport_icao in metar_data_cache:
        cache_entry = metar_data_cache[airport_icao]
        time_since_cache = (current_time - cache_entry['timestamp']).total_seconds()
        if time_since_cache < METAR_CACHE_DURATION:
            return cache_entry['metar']
    
    # Cache miss or expired - fetch new data
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={airport_icao}&format=raw"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'VATSIM-Control-Recs/1.0')
        
        with urllib.request.urlopen(req, timeout=5) as response:
            metar_text = response.read().decode('utf-8').strip()
        
        # Check if we got valid data (not empty and not an error message)
        if not metar_text or metar_text.startswith('No METAR') or metar_text.startswith('Error'):
            # No data available - blacklist it
            metar_blacklist.add(airport_icao)
            return ""
        
        # Cache the result
        metar_data_cache[airport_icao] = {
            'metar': metar_text,
            'timestamp': current_time
        }
        
        return metar_text
        
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Station doesn't exist - blacklist it permanently
            metar_blacklist.add(airport_icao)
            return ""
        # For other HTTP errors, return cached data if available
        if airport_icao in metar_data_cache:
            return metar_data_cache[airport_icao]['metar']
        return ""
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError, TimeoutError, Exception):
        # On other errors, return cached data if available (even if expired), otherwise empty string
        if airport_icao in metar_data_cache:
            return metar_data_cache[airport_icao]['metar']
        return ""


def get_wind_from_metar(airport_icao: str) -> str:
    """
    Extract wind information from METAR.
    
    Args:
        airport_icao: The ICAO code of the airport
        
    Returns:
        Wind string in format like "27005KT" or "27005G12KT" or "00000KT" or empty string if unavailable
    """
    metar = get_metar(airport_icao)
    if not metar:
        return ""
    
    # Parse wind from METAR
    # Wind format in METAR: DDDSSGggKT or DDDSSKMHor DDDSSKPH (Direction Speed Gust)
    # Also handle VRB for variable, and 00000KT for calm
    
    # Look for wind pattern: direction (3 digits or VRB), speed (2-3 digits), optional gust (G + 2-3 digits), units (KT/KMH/KPH/MPS)
    wind_pattern = r'\b(\d{3}|VRB)(\d{2,3})(G\d{2,3})?(KT|KMH|KPH|MPS)\b'
    match = re.search(wind_pattern, metar)
    
    if match:
        direction = match.group(1)
        speed = match.group(2)
        gust = match.group(3)  # Includes 'G' prefix if present
        units = match.group(4)
        
        # Check for calm winds
        if direction != 'VRB' and int(speed) == 0:
            return "00000KT"
        
        # Build wind string (always convert to KT for consistency)
        wind_str = f"{direction}{speed}"
        if gust:
            wind_str += gust
        
        # Add KT suffix (convert if needed, but METAR is usually in KT)
        if units == 'KT':
            wind_str += 'KT'
        elif units in ['KMH', 'KPH']:
            # Convert km/h to knots (1 knot = 1.852 km/h)
            speed_kt = round(int(speed) / 1.852)
            wind_str = f"{direction}{speed_kt:02d}"
            if gust:
                gust_kt = round(int(gust[1:]) / 1.852)
                wind_str += f"G{gust_kt:02d}"
            wind_str += 'KT'
        elif units == 'MPS':
            # Convert m/s to knots (1 knot = 0.514444 m/s)
            speed_kt = round(int(speed) / 0.514444)
            wind_str = f"{direction}{speed_kt:02d}"
            if gust:
                gust_kt = round(int(gust[1:]) / 0.514444)
                wind_str += f"G{gust_kt:02d}"
            wind_str += 'KT'
        else:
            wind_str += 'KT'
        
        return wind_str
    
    return ""


def get_wind_info(airport_icao: str, source: str = "metar") -> str:
    """
    Fetch current wind information with caching.
    
    Wind data is cached for 60 seconds to avoid excessive API calls.
    Supports two sources:
    - "metar": Uses METAR from aviationweather.gov (default)
    - "minute": Uses up-to-the-minute observations from weather.gov
    
    Args:
        airport_icao: The ICAO code of the airport
        source: Wind data source - "metar" or "minute" (default: "metar")
        
    Returns:
        Formatted wind string like "27005G12KT" or "27005KT" or empty string if unavailable
    """
    if source.lower() == "metar":
        return get_wind_from_metar(airport_icao)
    elif source.lower() == "minute":
        return get_wind_info_minute(airport_icao)
    else:
        # Default to METAR for unknown sources
        return get_wind_from_metar(airport_icao)