# Plan

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

- [ ] Capture feature screenshots for the leftos.dev showcase page and send them back (leftos.dev inbox request 20261010-081555): shots 01–09 into `X:\dev\leftos.dev\.tmp\vcr-shots\` (overview, flight board, weather briefing, flight info, diversion finder, Go To, Custom Groupings, optional historical stats and VFR alternatives), then an inbox request to `leftos.dev` with the file list, a caption per shot and corrections to its list (which follows `USER_GUIDE.md`).

## Backlog

- [ ] Fix callers that still unpack `get_flight_category()` as `(category, color)`: it returns a plain `str` (`backend/data/weather_parsing.py:617`), so `ui/modals/vfr_alternatives.py:190` and `:247` raise `ValueError` (the VFR Alternatives finder shows nothing), `ui/modals/diversion_modal.py:424` leaves the Wx column stuck at "...", and `ui/modals/flight_info.py:382` and `:468` break the VFR-flight weather warning.
- [ ] Pass the VATSIM feed to the diversion finder: `ui/modals/flight_info.py:1027` reads `getattr(self.app, "vatsim_data", None)`, but `VATSIMControlApp` (`ui/app.py`) never sets `vatsim_data`, so `DiversionModal` (`ui/modals/diversion_modal.py:225`) always gets `None` and its ATC column and "ATC Online" filter are empty.
- [ ] Escape the Go To multi-select marker: `ui/modals/goto_modal.py:406-408` builds `"[x] ..."` in an `OptionList` prompt, which Textual parses as markup, so selected items show no checkbox.

- [ ] Add `docs/README.md` as the docs' start page, linking `docs/ARCHITECTURE.md`, `docs/atis-filtering-and-runway-extraction.md`, `USER_GUIDE.md`, `CLAUDE.md` and this plan: the repo has no docs index, so the architecture entry point is reachable only from `CLAUDE.md` (line 83).
