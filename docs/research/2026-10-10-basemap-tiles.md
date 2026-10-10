# Basemap tiles for leftos.dev/weather after CARTO's key requirement

Research date: 2026-10-10. Every source below was read on that date. Facts marked **unverified** could not be confirmed from a primary page in this pass.

## Context

The weather page draws a Leaflet 1.9.4 map over `https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png`. Every tile now reads "API KEY REQUIRED · carto.com/basemaps/apikey". The page loads no third-party JavaScript and its privacy policy names CARTO and OpenStreetMap as tile sources. Traffic is well under 100k tiles/month, non-commercial, behind Cloudflare.

## 1. CARTO: what changed

- **Date and scope.** "Since 25 September 2026, raster tiles are no longer served without a key. ... (Before that date, the map stayed visible behind a watermark.)" "Only the raster basemaps require a key today. Vector styles still load without one", and CARTO says it may extend the requirement to vector. Source: [CARTO Basemaps FAQ](https://docs.carto.com/faqs/carto-basemaps).
- **Free tier.** Non-commercial use (personal, research, teaching, non-profit) is free up to 5,000,000 tile requests per calendar month (UTC), counted across all keys and both raster and vector; commercial use is free up to 1,000,000. Sources: [FAQ](https://docs.carto.com/faqs/carto-basemaps), [plans JSON](https://dashboard.basemaps.carto.com/v1/plans), [Basemap Terms §3.b, §12.c-d](https://carto.com/legal/basemap-terms/) (version 29 September 2026).
- **"Commercial Use"** is a site offered for a fee, generating revenue (including advertising), or run by a business in its trade; everything else is non-commercial ([Terms §1.g](https://carto.com/legal/basemap-terms/)). This hobby site qualifies as non-commercial.
- **Getting a key.** Free, no CARTO account, requested with an e-mail address at [carto.com/basemaps/apikey](https://carto.com/basemaps/apikey/); "There is no API to issue keys" ([plans JSON](https://dashboard.basemaps.carto.com/v1/plans)).
- **Key in the URL.** A `key` query parameter: `https://basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}.png?key=<key>` ([plans JSON](https://dashboard.basemaps.carto.com/v1/plans)). The FAQ's Leaflet example keeps the subdomain form, `https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png?key=YOUR_KEY` with `subdomains: 'abcd'` ([FAQ](https://docs.carto.com/faqs/carto-basemaps)). The [basemap-styles README](https://github.com/cartodb/basemap-styles/) documents `{scale}` = optional `@2x`, so `{r}` should still work; the `rastertiles/` path prefix is new relative to the current URL.
- **Domain restriction: yes.** "A key with website restrictions only serves tiles to pages on the hosts you listed in the Basemaps dashboard. Every other request gets a 403." Hosts match exactly (`www.example.com` and `example.com` are separate; `*.example.com` excludes the apex). A localhost key must be separate: a key that lists `localhost` cannot also list public sites ([FAQ](https://docs.carto.com/faqs/carto-basemaps)).
- **Referer is load-bearing.** `Referrer-Policy: no-referrer` "breaks every map"; `same-origin` breaks Leaflet raster layers; the browser default works ([FAQ](https://docs.carto.com/faqs/carto-basemaps)). Check what `nginx-weather.conf` and Cloudflare send before relying on a restricted key.
- **Attribution.** "© OpenStreetMap contributors, © CARTO", linking https://carto.com/attributions/ ([plans JSON](https://dashboard.basemaps.carto.com/v1/plans); [Terms §13](https://carto.com/legal/basemap-terms/)).
- **Privacy.** CARTO receives Request Data (IP, Referer, user-agent, key, timestamps), acts as the site's processor, truncates IPs on ingestion, keeps logs 30 days in the US ([Terms §1.p, §10.b-f](https://carto.com/legal/basemap-terms/)). The terms make the site operator the controller ([§10.c](https://carto.com/legal/basemap-terms/)): worth one sentence in the privacy policy.
- **Revocable.** Free access may be suspended or terminated "at any time ... for any or no reason, without prior notice" ([Terms §3.c](https://carto.com/legal/basemap-terms/)).

## 2. Alternatives

### Stadia Maps (Alidade Smooth Dark)

- **Auth:** keyless domain-based authentication for production websites, validated from the browser's `Origin`/`Referer`; no key in the HTML. localhost works without a key under strict rate limits. `Referrer-Policy: no-referrer` breaks it. Source: [Authentication](https://docs.stadiamaps.com/authentication/).
- **Free tier:** 200,000 credits/month, raster tile = 1 credit, "Commercial use not allowed" (so non-commercial qualifies), no overage on Free. Source: [Pricing](https://stadiamaps.com/pricing/).
- **URL:** `https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png` (EU: `tiles-eu.stadiamaps.com`), max zoom 20. Source: [Alidade Smooth Dark](https://docs.stadiamaps.com/map-styles/alidade-smooth-dark/).
- **Attribution:** `© Stadia Maps © OpenMapTiles © OpenStreetMap`, linked to stadiamaps.com/attribution, openmaptiles.org, openstreetmap.org/copyright ([same page](https://docs.stadiamaps.com/map-styles/alidade-smooth-dark/)).
- **Caching:** prohibits "proxying or caching access to our Services in any way" except client-local caching within HTTP headers or 7 days; "server-side caching is prohibited" ([Terms of Service](https://stadiamaps.com/terms-of-service/), effective 18 March 2026).

### MapTiler (dark styles, e.g. `dataviz-dark`, `streets-v4-dark`; style IDs **unverified**)

- **Auth:** key required as `?key=`; production keys can be restricted by "Allowed HTTP origins"; the default key "has no protection" ([API key docs](https://docs.maptiler.com/cloud/api/authentication-key/)).
- **Free tier:** "Suitable for testing, PoC, prototyping, personal, or non-commercial use", 100k API requests/month, "MapTiler logo on the map" required on Free ([Pricing](https://www.maptiler.com/cloud/pricing/)).
- **URL:** `https://api.maptiler.com/maps/{mapId}/256/{z}/{x}/{y}{r}.png?key=<key>` (pattern `maps/{mapId}/{tileSize/}/{z}/{x}/{y}{scale}.{format}`) ([Maps API](https://docs.maptiler.com/cloud/api/maps/)).
- **Attribution:** "© MapTiler © OpenStreetMap contributors" ([copyright](https://www.maptiler.com/copyright/)), plus the logo on Free.
- **Caching:** no caching clause found in a text search of the terms page (**unverified**).
- **Note:** the logo requirement is an image overlay; MapTiler's SDK is not needed for raster XYZ, but the logo must be added by the page.

### Esri (Dark Gray Canvas, `arcgis/dark-gray`)

- **Auth:** "An ArcGIS Location Platform account is required"; tiles are 512px, z/y/x order ([Static Basemap Tiles](https://developers.arcgis.com/rest/static-basemap-tiles/)). Style list includes `arcgis/dark-gray`, `arcgis/navigation-night`, `arcgis/streets-night`, `arcgis/human-geography-dark`.
- **URL:** `https://static-map-tiles-api.arcgis.com/arcgis/rest/services/static-basemap-tiles-service/v1/arcgis/dark-gray/static/tile/{z}/{y}/{x}?token=<access token>` (base URL and path from the doc above; `token` parameter name **unverified**). Leaflet needs `tileSize: 512, zoomOffset: -1`.
- **Free tier, referrer restriction, caching terms:** **unverified** (the pricing page did not render). Data partners include TomTom and Garmin, so attribution is longer than OSM-only and the privacy policy would gain a new processor.

### Thunderforest (Transport Dark is the only dark style found)

- **Free tier:** "Hobby Project", Free, 150,000 tile requests/month ([Pricing](https://www.thunderforest.com/pricing/)). Key restriction by domain **unverified**.
- **Style:** [Transport Dark](https://www.thunderforest.com/maps/transport-dark/) is a transit map; heavy rail/bus overlays are noise under weather data.
- **URL:** `https://tile.thunderforest.com/transport-dark/{z}/{x}/{y}{r}.png?apikey=<key>` (**unverified** from a primary page).
- **Attribution and caching:** credit "Thunderforest" and "OpenStreetMap contributors"; "Caching proxies or other redistribution methods are not permitted" ([Terms](https://www.thunderforest.com/terms/)).

### Jawg (Jawg Dark)

- **Free tier:** "Basic, Low traffic, Free", 25,000 map views/month, a map view = 15 raster tiles (about 375k tiles); non-commercial use "authorized for publicly available websites (no login), with no commercial purposes" ([Pricing](https://www.jawg.io/en/pricing/)).
- **URL, key restriction, attribution:** docs pages did not load; URL commonly `https://tile.jawg.io/jawg-dark/{z}/{x}/{y}{r}.png?access-token=<token>` (**unverified**).

### OpenStreetMap standard tiles (light only)

- **Policy:** keyless; use exactly `https://tile.openstreetmap.org/{z}/{x}/{y}.png`; visible attribution "© OpenStreetMap contributors"; a valid Referer must be sent and no restrictive Referrer-Policy; no bulk download; honour cache headers (7 days minimum); caching proxies are "generally not recommended" and must identify themselves; no SLA, blocks without notice ([Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/)).
- **Fit:** no dark style. A CSS `filter: invert(1) hue-rotate(180deg)` on the tile pane gives a dark look with no JS and no new provider, but it is a hack (colours shift, labels go odd).

### Self-hosting

- **Protomaps PMTiles** renders vector tiles in the browser with `pmtiles` + MapLibre or `protomaps-leaflet`; that is new JavaScript, acceptable only if vendored under `scripts/weather_daemon/assets/` like Leaflet. Not researched further here.
- **Pre-rendered raster** (render a z0-z8 dark raster pyramid once from OSM data and serve it from the server/Cloudflare) needs no JS and no third party at all, but needs a render toolchain and ODbL attribution.

## 3. Cloudflare caching

The site's own Cloudflare zone caches only what leftos.dev serves; tile requests go from the browser straight to the provider, so none of these clauses bite unless tiles are proxied through leftos.dev. If they were:

- CARTO forbids "proxying or caching the content on the server side" and offering itself as a proxy, and allows browser caching up to 30 days ([Terms §9.c.i-iii](https://carto.com/legal/basemap-terms/)).
- Stadia prohibits server-side caching ([Terms](https://stadiamaps.com/terms-of-service/)).
- Thunderforest forbids caching proxies ([Terms](https://www.thunderforest.com/terms/)).
- OSM discourages but tolerates an identified caching proxy ([Tile Usage Policy §5](https://operations.osmfoundation.org/policies/tiles/)).

A proxy would also strip the browser's Referer, which CARTO and Stadia domain auth depend on. Keep tiles direct.

## Comparison

| Provider | Key | Domain lock | Free for this site | Dark | Attribution | Server cache | Privacy-policy change |
|---|---|---|---|---|---|---|---|
| CARTO dark_all + key | Yes, `?key=` | Yes (hosts list) | Yes, 5M/month non-commercial | Yes (same look) | © OSM, © CARTO | Forbidden | None (already named); optional processor note |
| Stadia Alidade Smooth Dark | No (domain auth) | Yes, keyless | Yes, 200k credits, non-commercial only | Yes | © Stadia © OpenMapTiles © OSM | Forbidden | Swap CARTO for Stadia |
| MapTiler dark | Yes, `?key=` | Yes (origins) | Yes, 100k requests, logo required | Yes | © MapTiler © OSM + logo | Unverified | Swap CARTO for MapTiler |
| Jawg Dark | Yes | Unverified | Yes, ~375k tiles | Yes | Unverified | Unverified | Swap |
| Esri dark-gray | Yes, token | Unverified | Unverified | Yes | Esri + partners | Unverified | Swap, adds Esri |
| Thunderforest Transport Dark | Yes | Unverified | Yes, 150k | Transit only | © Thunderforest © OSM | Forbidden | Swap |
| OSM standard | No | n/a | Yes, best-effort | No (CSS invert only) | © OSM | Discouraged | Drop CARTO |
| Self-hosted raster | No | n/a | Yes | Yes | © OSM (ODbL) | Own | Drop CARTO |

## Recommendation

Ranked by: no or domain-restricted key, free for this use, dark style, minimal privacy-policy change.

1. **CARTO with a free non-commercial key, restricted to `leftos.dev` (and a separate localhost key for `LocalTest.ps1`).** Same tiles and look, 50x headroom on the quota, privacy policy already names CARTO. Change the URL to `https://{s}.basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}{r}.png?key=<key>`. A domain-locked key in public HTML is the documented model. Risks: terms are revocable at will and CARTO says vector may follow; confirm the site does not send `Referrer-Policy: no-referrer` or `same-origin`.
2. **Stadia Alidade Smooth Dark with domain auth.** No key at all in the page, a close visual match, free for non-commercial use at 200k tiles. Needs a Stadia account, an attribution change and a privacy-policy swap. Best fallback, or first choice if a key in the HTML is unwanted.
3. **MapTiler dark** with an origin-locked key: workable, but the Free-plan logo and 100k cap make it third.
4. Jawg, Esri, Thunderforest: each needs more verification or has a poorer style fit.
5. **Self-hosted raster pyramid**: the only option with no third party; worth it only if both 1 and 2 fail.

## References

- [CARTO Basemaps FAQ](https://docs.carto.com/faqs/carto-basemaps) - key requirement date, free tiers, website restrictions, Referer
- [CARTO plans JSON](https://dashboard.basemaps.carto.com/v1/plans) - tiers, URL templates, attribution
- [CARTO Basemap Terms, 29 Sep 2026](https://carto.com/legal/basemap-terms/) - commercial definition, caching, privacy
- [CARTO API key page](https://carto.com/basemaps/apikey/) - request form
- [cartodb/basemap-styles](https://github.com/cartodb/basemap-styles/) - URL structure
- [Stadia authentication](https://docs.stadiamaps.com/authentication/), [pricing](https://stadiamaps.com/pricing/), [Alidade Smooth Dark](https://docs.stadiamaps.com/map-styles/alidade-smooth-dark/), [terms](https://stadiamaps.com/terms-of-service/)
- [MapTiler API key](https://docs.maptiler.com/cloud/api/authentication-key/), [pricing](https://www.maptiler.com/cloud/pricing/), [Maps API](https://docs.maptiler.com/cloud/api/maps/), [copyright](https://www.maptiler.com/copyright/)
- [Esri Static Basemap Tiles](https://developers.arcgis.com/rest/static-basemap-tiles/)
- [Thunderforest pricing](https://www.thunderforest.com/pricing/), [terms](https://www.thunderforest.com/terms/), [Transport Dark](https://www.thunderforest.com/maps/transport-dark/)
- [Jawg pricing](https://www.jawg.io/en/pricing/)
- [OSMF Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/)
