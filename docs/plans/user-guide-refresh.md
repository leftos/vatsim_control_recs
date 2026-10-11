# User guide refresh with screenshots

Bring `USER_GUIDE.md` up to the current app, section by section, and give each feature section a screenshot that a committed script regenerates.

## Owner rulings

- **Scope:** a full audit of every section of `USER_GUIDE.md` against the code (shortcuts, columns, modal contents, Go To, the grouping builder, my groupings, briefings, route weather, ETA and ARR rules), rewriting what is stale, plus a screenshot in each feature section.
- **Data:** screenshots come from the fixture snapshot `data/test-vatsim-data.json` (2,020 pilots, 263 controllers, 168 ATIS, taken 2025-10-25T20:03Z) with a fixed clock, so a recapture after a UI change differs only where the UI did. Weather is frozen too: the capture script records one weather snapshot (METARs, TAFs, D-ATIS for the captured airports) into a committed fixture beside the traffic and captures from it, with the clock frozen at the weather snapshot's recording time so TAF and METAR times read naturally; live weather would change every image on every recapture.
- **Image format:** open. Textual's native SVG export versus a rasterized PNG, decided from a rendered comparison of the first capture.

## Tasks

- [ ] Capture script `scripts/capture_guide_screenshots.py`: drives the app headless (`run_test`) on the fixture with a fixed clock and a filter, opens each screen the guide describes, and writes one image per screen under `docs/images/guide/`.
- [ ] Audit: every `USER_GUIDE.md` section checked against the code, with each stale statement listed by line and the code that contradicts it.
- [ ] Format decision from the rendered comparison.
- [ ] Rewrite `USER_GUIDE.md` from the audit, embedding the screenshots.
