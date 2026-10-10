# Plan

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

- [ ] Convert the project from pip to uv: `requirements.txt`, the `.venv` bootstrap in `main.py` (`ensure_venv_and_restart` line 117, `ensure_requirements_installed` line 237), the spaCy model download, `.github/workflows/build-release.yml` (lines 43–48), the weather-daemon scripts (`scripts/weather_daemon/service/setup.sh` lines 65–73, `deploy.sh` line 99, `Deploy.ps1` line 67, `LocalTest.ps1` lines 53–63) and the setup docs (`CLAUDE.md`, `README.md`, `USER_GUIDE.md`).

  Settled: `uv run main.py` is the entry point and the venv/pip bootstrap in `main.py` is removed; `pyproject.toml` and `uv.lock` replace `requirements.txt`; `en_core_web_sm` is a locked dependency pinned to its wheel URL, with no runtime download; the weather-daemon server scripts move to uv too (`setup.sh` installs uv, deploys run `uv sync`), so the next deploy needs uv on the server.

## Next up

- [ ] Finish the leftos.dev showcase screenshots and send them back (leftos.dev inbox request 20261010-081555). In `X:\dev\leftos.dev\.tmp\vcr-shots\`: 01–05 and 09 are taken; 06 (Go To) and 07 (Custom Groupings) need a retake; 08 is skipped (no StatSim key).

  The driver is `.tmp/vcr-shots-driver/drive.py` (untracked; its `install_bug_shims()` and the `app.vatsim_data = data` line in `do_diversion` come out, since the bugs they covered are fixed). The reply draft with captions and corrections to the request's list is `.tmp/vcr-shots-reply.md`; send it as an inbox request to `leftos.dev`.
- [ ] Profile the app under heavy traffic and plan the wins: drive it headless (Textual `run_test`, the screenshot driver's harness) with a large tracked set and with no filter, which today bogs the app down, and time startup (`main.py` → `analyze_flights_data()` in `backend/core/analysis.py`, the disambiguator's `get_pretty_names_batch`), one refresh cycle (`ui/app.py` refresh worker), and table updates (`ui/tables.py`, `widgets/split_flap_datatable.py`). Use stdlib `cProfile` and `uvx py-spy` (no new dependency); write the findings to a subplan with measured hotspots before any fix.
- [ ] Make the Go To modal's mode and keys visible (R1 in [goto-ux-review.md](./goto-ux-review.md)): a title naming the mode, a one-line hint computed from mode, focus and the highlighted row, focus on the list when multi-select starts, and a toast for every key that does nothing (`ui/modals/goto_modal.py`).
- [ ] Fix Go To edit mode reading and writing a picked airport's dep/arr filter under different keys, so the row shows the wrong filter and F cannot change what is saved (`ui/modals/goto_modal.py:689-691` against `:403-405` and `:761-765`).

## Backlog

- [ ] Add `docs/README.md` as the docs' start page, linking `docs/ARCHITECTURE.md`, `docs/atis-filtering-and-runway-extraction.md`, `USER_GUIDE.md`, `CLAUDE.md` and this plan: the repo has no docs index, so the architecture entry point is reachable only from `CLAUDE.md` (line 83).
- [ ] Raise ruff's `target-version` from `py310` (`ruff.toml:6`) to `py313` to match `requires-python` in `pyproject.toml`, and land the UP-rule rewrites it triggers as their own commit.
- [ ] Tracked Airports Manager: the hint offers Space to select but the `ListView` toggles only on Enter (`ui/modals/tracked_airports.py:117`), and `S` saves the whole tracked list, ignoring the picked rows (`:243`).
- [ ] Ctrl+D still deletes a favorite inside the Weather Briefing grouping picker, which reuses the Go To screen (`ui/modals/goto_modal.py:223`).
- [ ] A favorite's saved dep/arr filters apply only when it is opened from Go To, not from Enter on the Custom Groupings tab (`ui/app.py` flight-board open path).
- [ ] Unhandled `"airports"` result type in `ui/app.py:1135`.
- [ ] Investigate the MEA check: when an airway's entry or exit fix is not found, every segment of the airway is checked (`backend/data/navaids.py:1165-1175`), so `ABQ V12 ONM` reported a California V12 segment at 29,000 ft. Seen by the screenshot run, not yet confirmed.
- [ ] Investigate the MEA warning comparing only the first filed altitude, so step climbs are ignored (`ui/modals/flight_info.py:661-668`); live UAE82M filed step climbs to FL400 and was warned against a 33,000 ft MEA. Not yet confirmed.
- [ ] Investigate the overview's NEXT ETA showing `52h06m` for LFBO, from one long-haul flight from Melbourne. Not yet confirmed.
- [ ] Investigate display names that fall back to the ICAO code ("ETIK - Etik" in the VFR finder) and two UK weather-briefing areas both named "LONDON AREA". Not yet confirmed.
- [ ] Go To grouping builder (R3 in [goto-ux-review.md](./goto-ux-review.md)): a separate screen that adds any airport from the unified data, lists every member with its filter, removes or excludes members, and asks before overwriting, shadowing a preset or discarding; grouping file I/O moves into `backend/core/groupings.py`.
- [ ] Go To list hygiene (R5 in [goto-ux-review.md](./goto-ux-review.md)): ★ groupings first with section headers, a "showing 100 of N" row, collapsed members for selected groupings, and the dead `filter_type="$"` path removed (`ui/app.py` `_build_goto_results`, `ui/modals/goto_modal.py`).
- [ ] One name for saved groupings, and filters that follow them (R4 in [goto-ux-review.md](./goto-ux-review.md)): "my groupings" (★) everywhere instead of favorite, grouping and custom grouping, and saved dep/arr filters applied wherever the grouping is opened. This also closes the Custom Groupings tab filter item above.
