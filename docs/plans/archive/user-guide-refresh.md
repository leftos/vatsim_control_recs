# User guide refresh with screenshots

Bring `USER_GUIDE.md` up to the current app, section by section, and give each feature section a screenshot that a committed script regenerates.

## Owner rulings

- **Scope:** a full audit of every section of `USER_GUIDE.md` against the code (shortcuts, columns, modal contents, Go To, the grouping builder, my groupings, briefings, route weather, ETA and ARR rules), rewriting what is stale, plus a screenshot in each feature section.
- **Data:** screenshots come from the fixture snapshot `data/test-vatsim-data.json` (2,020 pilots, 263 controllers, 168 ATIS, taken 2025-10-25T20:03Z) with a fixed clock, so a recapture after a UI change differs only where the UI did. Weather is frozen too: the capture script records one weather snapshot (METARs, TAFs, D-ATIS for the captured airports) into a committed fixture beside the traffic and captures from it, with the clock frozen at the weather snapshot's recording time so TAF and METAR times read naturally; live weather would change every image on every recapture.
- **Image format:** SVG, Textual's own screenshot export: recaptures are byte-identical, GitHub renders it in Markdown, and no rasterizer dependency is needed.
- **Names:** the capture replaces every pilot's and controller's name from the fixture with a placeholder, so no real person's name is published.

## Tasks

- [x] Capture script `scripts/capture_guide_screenshots.py`: drives the app headless (`run_test`) on the fixture with a frozen clock and weather snapshot and the "N90 New York Combined" grouping, and writes one SVG per screen under `docs/images/guide/` (17 screens; Historical Statistics needs an API key).
- [x] Audit: every `USER_GUIDE.md` section checked against the code.
- [x] Format decision.
- [x] Rewrite `USER_GUIDE.md` from the audit, embedding the screenshots.
