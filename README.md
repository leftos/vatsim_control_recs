# VATSIM Control Recommendations

```text
usage: vatsim_control_recs_ui.py [-h] [--max-eta-hours MAX_ETA_HOURS] [--refresh-interval REFRESH_INTERVAL] [--airports AIRPORTS [AIRPORTS ...]] [--groupings GROUPINGS [GROUPINGS ...]] [--supergroupings SUPERGROUPINGS [SUPERGROUPINGS ...]]

Analyze VATSIM flight data and controller staffing

options:
  -h, --help            show this help message and exit
  --max-eta-hours MAX_ETA_HOURS
                        Maximum ETA in hours for arrival filter (default: 1.0)
  --refresh-interval REFRESH_INTERVAL
                        Auto-refresh interval in seconds (default: 15)
  --airports AIRPORTS [AIRPORTS ...]
                        List of airport ICAO codes to include in analysis (default: all)
  --groupings GROUPINGS [GROUPINGS ...]
                        List of custom grouping names to include in analysis (default: all)
  --supergroupings SUPERGROUPINGS [SUPERGROUPINGS ...]
                        List of custom grouping names to use as supergroupings. This will include all airports in these supergroupings and any detected sub-groupings.
```

**This project includes IATA/ICAO List data available from <http://www.ip2location.com>.**

IATA is a registered trademark of International Air Transport Association.  
ICAO is a registered trademark of International Civil Aviation Organization.  
All other product names mentioned on this repository may be trademarks or registered trademarks of their respective companies.  
