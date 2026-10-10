# ARR predicate vs flight board: debugger report

Status: **resolved**

Root cause: the overview's ARR column admits an airborne inbound on great-circle distance over groundspeed (`backend/core/flights.py:130-145`, called from `backend/core/analysis.py:327`), while every ETA the user sees (each board row's ETA, and the NEXT ETA, `analysis.py:333`) comes from `calculate_eta`. That ETA is never lower than (great circle − 5 nm) / groundspeed and is usually higher. So ARR counts flights whose displayed ETA is past `max_eta_hours`, and NEXT ETA can exceed the window it is supposed to sit inside.

The plan item's premise is half wrong. The app's flight board never applies the window: its only caller passes `max_eta_hours=0` (`ui/modals/flight_board.py:404`, "Always show all arrivals on the flight board", commit 9823828). The `<= max_eta_hours` tests at `flights.py:448` and `:474` are unreachable in the app, and no ARR-counted flight is ever missing from its board (446 boards checked, 0 missing). What the user does see is ARR counting rows whose board ETA is past the window. BAW372's 1.0002 h is the overview's ETA. The board's own ETA for it is 0.9975 h ("59m").

A second parting shows up alongside it. `load_airport_data` (`analysis.py:53-61`) drops `elevation`, so the overview's `calculate_eta` assumes a sea-level field, while the board passes the unified data, which has elevation. NEXT ETA and the board's ETA for the same flight differ for 1148 of the 1417 airborne inbounds, for example KDEN 7m vs 3m.

## Measurements

Rig: `data/test-vatsim-data.json` (2020 pilots), loaded in-process with sockets, `urllib` and `requests` blocked. The reference data came from `data/raw/*`, and NASR 2026-10-01 and CIFP 2610 from the existing user cache. Scripts lived under `X:/dev/vatsim_control_recs.wt/dbg-arr-predicate/.tmp/` (`arr_probe_common.py`, `arr_probe_counts.py`, `arr_probe_detail.py`, `arr_probe_timing.py`, `arr_probe_timing2.py`) and ran with `uv run python .tmp/<script>.py`. Candidates are the flights that reach the overview's airborne branch with the window off. The allowlist was every airport with coordinates: 2015 flights, of which 1417 are airborne inbounds to 446 airports.

### (1) What each predicate admits

| | W = 1.0 h | W = 2.0 h |
|---|---|---|
| great circle / gs <= W (overview ARR today) | 820 | 1048 |
| calculate_eta <= W, board's airport dict (with elevation) | 797 | 1029 |
| calculate_eta <= W, overview's airport dict (elevation dropped) | 795 | 1029 |
| admitted by great circle only | 23 | 19 |
| admitted by calculate_eta only | 0 | 0 |
| of the great-circle-only flights, pushed past W by route distance alone | 9 | 14 |
| of them, pushed past W by the descent / 250 kt / approach model alone | 14 | 5 |
| airports whose airborne ARR differs from the board rows with ETA <= W | 22 of 333 | 14 of 391 |
| airports whose NEXT ETA is past the window | 6: CYTR 1h32m, KFXE 1h22m, KORF 1h19m, LFBO 1h00m, TKPK 30h19m, ZWWW 1h02m | 6: KJAX 2h01m, KPBI 2h29m, KRSW 2h40m, NCRG 2h00m, PHLI 21h37m, TKPK 30h19m |
| cheap bound (gc - 5 nm) / gs <= W admits | 827, misses 0 calculate_eta admits | 1049, misses 0 |

Samples, great-circle ETA against calculate_eta (board dict):

- RYR81XD ->EGSS: 394.0 nm at 415 kt, 0.9493 h against 1.0101 h; board shows "1h00m", ARR counts it.
- FIN1101 ->EFHK: 0.9505 h against 1.0115 h, "1h00m".
- EWG86U ->EDDL: 0.9640 h against 1.0246 h, "1h01m".
- CXA259 EHAM->CYTR: 0.551 h against 1.540 h, mostly route distance (0.614 h without the route); ARR 1, NEXT ETA 1h32m.
- WIA357 TNCM->TKPK: 44 nm out, 0.314 h against 30.328 h (0.346 h without the route `VECTORXZPATA/10 PJM A517 SKB`).
- AAL10 KLAX->KJFK (W = 2): 1.793 h against 8.839 h (1.868 h without the route).
- BAW372 ->LFBO: 422.99 nm at 455 kt, 0.92965 h; overview dict 1.000216 h, board dict 0.997518 h.

calculate_eta comes out below the great-circle ETA for only 3 flights (PUA1496, DLH98X, DLH470, all within 10 nm, approach speed above groundspeed). The ratio calculate_eta / gc ETA has min 0.9009, median 1.0906 and max 121.08.

Real-code check, W = 1.0 (`analyze_flights_data` with a one-airport allowlist, and `get_airport_flight_details` as the app calls it, window 0):

- EGSS: ARR 2, NEXT ETA 19m; the board shows RYR60RY 19m, RYR81XD 1h00m, RYR1B 1h14m and 3 not yet departed.
- EDDL: ARR 5; the board's airborne rows are 2m, 6m, 7m, 35m, EWG86U 1h01m, CFG069 1h07m and 6h06m.
- ESSA: ARR 13, including SWR7LM, which the board shows at 1h00m.
- CYTR: ARR 1, NEXT ETA 1h32m, board CXA259 1h32m.

Elevation: 1148 of 1417 candidates get a different calculate_eta from the overview dict than from the board dict:

- ZSEGP ->FAGM (5483 ft): 9m against 1m.
- VSP950 ->SEQM: 16m against 9m.
- UAL3316 ->KDEN: 7m against 3m.

### (2) Cost

Component times, median of 9, 1417 candidates:

| | W = 1.0 | W = 2.0 |
|---|---|---|
| great-circle check, all candidates | 1.84 ms (1.30 us/flight) | 1.79 ms |
| cheap bound check, all candidates | 1.25 ms | 1.34 ms |
| calculate_eta, all candidates, warm route cache | 29.65 ms (20.9 us/flight) | 27.01 ms (19.1 us/flight) |
| calculate_eta, all candidates, cold route cache (median of 5) | 246 ms (174 us/flight) | 263 ms (186 us/flight) |
| calculate_eta over the great-circle admits (what `analysis.py:333` does today) | 11.07 ms (820) | 16.17 ms (1048) |
| calculate_eta over the bound admits (option a, prefiltered) | 11.29 ms (827) | 16.71 ms (1049) |

The first `calculate_eta` pass of a process (NASR/CIFP index load plus route parsing) took 1.196 s. It is paid today already.

Whole `analyze_flights_data`, no filter (`airport_allowlist=None`, the perf doc's flight-heavy load). Median of 15 runs, 3 variants interleaved, option (a) emulated by patching the predicate with a memoised `calculate_eta`. Run-to-run spread between min and median is 10 to 18 ms.

| | W = 1.0 | W = 2.0 |
|---|---|---|
| current | 142.7 ms, sum ARR 966 | 146.8 ms, sum ARR 1193 |
| (a), calculate_eta on every airborne inbound | 163.1 ms, 1408 calls, sum ARR 941 | 157.2 ms, 1408 calls, sum ARR 1174 |
| (a), great-circle bound first | 152.1 ms, 820 calls, sum ARR 941 | 141.9 ms, 1041 calls, sum ARR 1174 |

Analysis already computes `calculate_eta` for every counted flight (`analysis.py:333`, from a9f64be). The prefiltered form of (a) therefore makes as many `calculate_eta` calls as today, plus the 7 (W = 1) or 1 (W = 2) the 5 nm slack admits. Its cost is inside the noise. The unfiltered form adds about 18 ms per warm refresh, and about 100 ms the first time the roughly 600 extra routes are parsed (597 x ~175 us, derived, not timed separately).

### (3) What the user sees

- **ARR counts a flight the board shows past the window.** Measured: 22 of 333 airports at 1 h, 14 of 391 at 2 h, for example EGSS ARR 2 with the board showing RYR81XD at "1h00m" (1.0101 h).
- **The opposite never happens.** No airborne flight with board ETA <= W is left out of ARR (calculate_eta-only: 0 at both windows).
- **NEXT ETA exceeds the window** at 6 airports per window, for example TKPK "30h19m" under a 1 h window.
- **NEXT ETA disagrees with the same flight's board ETA** at high fields because of the elevation drop.
- **"ARR says 3, board lists 2" does not occur.** The board lists every inbound (window off), including not-yet-departed ones at "----", so it always lists at least ARR's airborne flights.

## Root cause

- `backend/core/flights.py:142-145`: `eta_hours = distance / groundspeed`, compared to `max_eta_hours`. This is the ARR admission, called at `backend/core/analysis.py:327`.
- `backend/core/analysis.py:333`: the counted flight's ETA comes from `calculate_eta`, a different model (route distance when longer, 3:1 descent, 250 kt below 10,000 ft, approach speed for the last 5 nm). Proven from `calculations.py:146-214`: calculate_eta >= (gc - 5 nm) / gs. Measured: it is at or above gc / gs for 1414 of 1417 flights.
- `backend/core/analysis.py:56-60`: `load_airport_data` keeps latitude, longitude and country_code only. `calculations.py:139` then reads elevation 0 for the overview, while the board passes `config.UNIFIED_AIRPORT_DATA` (`flight_board.py:406`), which carries elevation.
- `ui/modals/flight_board.py:404`: the board's only call passes window 0, so `flights.py:447-448` and `:473-474` never filter in the app. `FlightBoardScreen.max_eta_hours` (`flight_board.py:181`) is stored and never read.

Who depends on it:

- `is_flight_flying_near_arrival` is called at `analysis.py:327` (windowed) and `flights.py:432` (window 0).
- `calculate_eta` is called at `analysis.py:333`, `flights.py:444` and `:470`, and `ui/modals/flight_info.py:919`.
- `load_airport_data`'s dict feeds `analysis.py:213` onward (spatial on-ground checks, `filter_flights_by_airports`, `get_staffed_positions`). Adding a key changes none of those.

## Options

**(a) Both use calculate_eta <= max_eta_hours.** The overview gates on gs >= 40 and gc <= W x gs + 5 nm (sound by the bound above; misses 0 of 797/1029), then on calculate_eta <= W, and reuses that value for NEXT ETA.

- Cost: none measurable (11.29 against 11.07 ms component; whole-analysis medians within noise).
- Result: ARR equals the board's rows with ETA <= W by construction, and NEXT ETA is always <= W.
- Worst case: calculate_eta's route-distance faults now decide ARR. WIA357, 44 nm from TKPK (0.31 h), leaves ARR at 30.3 h; route distance is the cause for 9 of 23 dropped flights at 1 h and 14 of 19 at 2 h, including CONUS routes (AAL10 KLAX->KJFK 8.8 h, GJS4430 KEWR->KORF 1.33 h against 0.40 h). Today the same flights are counted but show those ETAs on the board and as NEXT ETA, so the fault is visible either way. The route-distance bound in MAIN.md's next item removes most of it.
- Unfiltered form: +18 ms per warm refresh, about +100 ms on first parse.

**(b) Both use great circle / groundspeed.** In the app the board has no window, so (b) changes nothing a user sees.

- Cost: zero.
- Worst case: today's state persists. ARR keeps counting rows the board shows past the window (22/333 airports at 1 h), and NEXT ETA keeps exceeding the window (TKPK 30h19m) unless it is also clamped, which would make NEXT ETA disagree with the board.

**(c) Other.**

- (c1) Remove the dead window from the board path: the `max_eta_hours` parameter of `get_airport_flight_details` and the two `<= max_eta_hours` tests, plus `FlightBoardScreen.max_eta_hours`, since the only caller passes 0. It costs nothing, fixes nothing visible, and removes the second predicate the plan item names.
- (c2) Carry elevation in `load_airport_data`. Needed with (a) or (b), or the overview's ETA disagrees with the board's for the same flight (795 against 797 admits at 1 h, KDEN 7m against 3m).

## Recommendation and minimal fix

Take (a) with the bound, plus (c2), and fold (c1) in so one predicate exists. Land it with or after the route-distance bound (MAIN.md, "Non-US filed routes ...") to avoid the WIA357-style drops.

- `backend/core/analysis.py` `load_airport_data`: add `"elevation": info.get("elevation")` to each entry.
- `backend/core/flights.py`: replace the window logic of `is_flight_flying_near_arrival` with one function that the overview and the board both call, for example `arrival_eta_in_window(flight, airports, aircraft_approach_speeds, max_eta_hours) -> float | None`. It returns None unless the flight is airborne inbound (the current gs >= 40 / known arrival / coordinates checks). With `max_eta_hours == 0` it returns `calculate_eta(...)[2]`. Otherwise it returns None when `(gc - 5) / gs > max_eta_hours` (a sound lower bound, so no `calculate_eta` call is wasted), else the ETA when `<= max_eta_hours`, else None.
- `backend/core/analysis.py:327-335`: the airborne branch calls the helper and feeds `earliest_arrival_eta` with the returned hours, with no second `calculate_eta`.
- Board path: `get_airport_flight_details` drops `max_eta_hours` (the app always shows all arrivals) and uses the helper with window 0 to decide "airborne inbound". `flight_board.py` drops the argument and `FlightBoardScreen.max_eta_hours`.
- Do not touch: the on-ground branches, the `arrivals_all` counting, the board's choice to list all inbounds (including the "----" ones), `calculate_eta`'s model and the route-distance code.

## Failing test

`tests/test_arrival_window.py`, both tests red today (run from scratch against 1df8cac: 2 failed, and `tests/test_next_eta_window.py` 3 passed):

- `test_arr_leaves_out_an_inbound_whose_eta_is_past_the_window` fails with `ARR 2 counts BAW1, whose ETA on the board is 1.029 h` / `assert (2, 2) == (1, 2)`. Its precondition (great circle <= 1.0 < calculate_eta) passes, so it fails on the predicate and not on the fixture. The expectation is the flight's own `calculate_eta`, the value the board displays, so it is not the code agreeing with itself.
- `test_next_eta_matches_the_board_at_a_high_field` fails with `'8m' == '7m'` (overview elevation 0 against the board's 5431 ft). It is red for (c2).

Fixtures use empty routes, so no NASR/CIFP is touched under the conftest's isolated `LOCALAPPDATA`.

```python
"""The ARR column counts an airborne inbound exactly when the ETA the flight board shows is inside max_eta_hours."""

from typing import Any, cast

import pytest

from airport_disambiguator import AirportDisambiguator
from backend.cache.manager import load_aircraft_approach_speeds
from backend.core import analysis
from backend.core.calculations import calculate_eta, haversine_distance_nm
from backend.core.flights import get_airport_flight_details

UNIFIED_AIRPORT_DATA: dict[str, dict[str, Any]] = {
    "LFBO": {"latitude": 43.6291007996, "longitude": 1.3638199568, "country": "FR"},
    "LFPG": {"latitude": 49.0127983093, "longitude": 2.5499999523, "country": "FR"},
    "KDEN": {
        "latitude": 39.8616,
        "longitude": -104.6731,
        "country": "US",
        "elevation": 5431,
    },
    "KORD": {
        "latitude": 41.9786,
        "longitude": -87.9048,
        "country": "US",
        "elevation": 680,
    },
}
SPEEDS = load_aircraft_approach_speeds("data/aircraft_data.csv")

# 440 nm north of LFBO at 460 kt: 0.957 h over the great circle, past 1 h once descent and the 250 kt limit count.
CRUISING_PAST_THE_WINDOW: dict[str, Any] = {
    "callsign": "BAW1",
    "latitude": 50.9624,
    "longitude": 1.3638,
    "groundspeed": 460,
    "altitude": 37000,
    "flight_plan": {
        "departure": "EGLL",
        "arrival": "LFBO",
        "aircraft_short": "A320",
        "route": "",
    },
}

ON_DESCENT_INTO_LFBO: dict[str, Any] = {
    "callsign": "AFR2",
    "latitude": 44.2291,
    "longitude": 1.3638,
    "groundspeed": 250,
    "altitude": 9000,
    "flight_plan": {
        "departure": "LFPG",
        "arrival": "LFBO",
        "aircraft_short": "A320",
        "route": "",
    },
}

# 30 nm east of KDEN, still above 10,000 ft: the field's 5,431 ft elevation shortens the descent.
DESCENDING_INTO_KDEN: dict[str, Any] = {
    "callsign": "UAL3",
    "latitude": 39.8616,
    "longitude": -104.0216,
    "groundspeed": 300,
    "altitude": 11949,
    "flight_plan": {
        "departure": "KORD",
        "arrival": "KDEN",
        "aircraft_short": "B738",
        "route": "",
    },
}


class _NoDisplayNames:
    def get_full_names_batch(self, _icaos: list[str]) -> dict[str, str]:
        return {}


def _board_eta_hours(pilot: dict[str, Any]) -> float:
    flight = {**pilot, "arrival": pilot["flight_plan"]["arrival"]}
    return calculate_eta(flight, UNIFIED_AIRPORT_DATA, SPEEDS)[2]


def _row(
    monkeypatch: pytest.MonkeyPatch,
    icao: str,
    pilots: list[dict[str, Any]],
    max_eta_hours: float,
) -> Any:
    feed = {"general": {}, "pilots": pilots, "controllers": [], "atis": []}
    monkeypatch.setattr(analysis, "download_vatsim_data", lambda: feed)
    airport_rows, _, _, _, _ = analysis.analyze_flights_data(
        max_eta_hours=max_eta_hours,
        airport_allowlist=[icao],
        include_all_arriving=True,
        unified_airport_data=UNIFIED_AIRPORT_DATA,
        disambiguator=cast(AirportDisambiguator, _NoDisplayNames()),
    )
    assert airport_rows is not None
    (row,) = [r for r in airport_rows if r.icao == icao]
    return row


def test_arr_leaves_out_an_inbound_whose_eta_is_past_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lfbo = UNIFIED_AIRPORT_DATA["LFBO"]
    great_circle_hours = (
        haversine_distance_nm(
            CRUISING_PAST_THE_WINDOW["latitude"],
            CRUISING_PAST_THE_WINDOW["longitude"],
            lfbo["latitude"],
            lfbo["longitude"],
        )
        / CRUISING_PAST_THE_WINDOW["groundspeed"]
    )
    assert great_circle_hours <= 1.0 < _board_eta_hours(CRUISING_PAST_THE_WINDOW)

    row = _row(
        monkeypatch,
        "LFBO",
        [CRUISING_PAST_THE_WINDOW, ON_DESCENT_INTO_LFBO],
        max_eta_hours=1.0,
    )

    assert (row.arrivals, row.arrivals_all) == (1, 2), (
        f"ARR {row.arrivals} counts BAW1, whose ETA on the board is {_board_eta_hours(CRUISING_PAST_THE_WINDOW):.3f} h"
    )


def test_next_eta_matches_the_board_at_a_high_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row(monkeypatch, "KDEN", [DESCENDING_INTO_KDEN], max_eta_hours=1.0)
    _, board_arrivals = get_airport_flight_details(
        "KDEN",
        0,
        None,
        UNIFIED_AIRPORT_DATA,
        SPEEDS,
        {"pilots": [DESCENDING_INTO_KDEN]},
    )
    (board_row,) = board_arrivals

    assert row.next_eta == board_row.eta_display
```

If (c1) removes `get_airport_flight_details`'s window argument, the second test's call drops the `0`.

## Hypotheses ruled out

- **The board leaves ARR-counted flights off.** Not in the app: the board passes window 0 (`flight_board.py:404`), and 0 of the ARR-counted airborne flights are missing across 446 boards.
- **calculate_eta admits flights the great circle rejects.** 0 at both windows. calculate_eta >= (gc - 5) / gs by construction, and only 3 flights fall below gc / gs, all inside 10 nm.
- **BAW372 is off the board at 1.0002 h.** That figure is the overview dict's elevation-0 ETA. The board's ETA is 0.9975 h, so it shows "59m".
- **Option (a) needs a costly calculate_eta pass every refresh.** With the 5 nm bound it makes the same calls analysis already makes (820 against 820 at 1 h). Whole-analysis medians: 152.1 against 142.7 ms at 1 h, 141.9 against 146.8 ms at 2 h, inside the 10 to 18 ms noise.

## Doc drift

- `CLAUDE.md:114` "Arrival: ... in-flight within `max_eta_hours` of arrival" sits under "ETA Calculation" (`:104-108`), which describes the calculate_eta model, but the overview admits on great circle / groundspeed (already in MAIN.md:54). It becomes true after (a).
- `CLAUDE.md:110` "NEXT ETA ... a flight beyond `max_eta_hours` ... never feeds it" is false today: NEXT ETA exceeds the window at 6 airports per window (TKPK 30h19m at 1 h). `USER_GUIDE.md:203-204` makes the same claim.
- `CLAUDE.md:107` "final 20nm": the code uses 5 nm (`calculations.py:189`), already in MAIN.md:54.
- `docs/plans/MAIN.md:42`: "left off the board at 1.0002 h". The app's board applies no window, and 1.0002 h is the overview's elevation-0 ETA (the board's is 0.9975 h). The item should name the visible mismatch: ARR counts rows the board shows past the window, and NEXT ETA exceeds it.
- `USER_GUIDE.md:289-296` (Flight Boards) does not say the board lists every inbound regardless of `--max-eta-hours`, including not-yet-departed ones at "----". `USER_GUIDE.md:146` says flights further out "are excluded", which is true only of the overview.
- `flights.py:196` documents the `max_eta_hours` filter of `get_airport_flight_details`, but the only caller passes 0, and `FlightBoardScreen.max_eta_hours` (`flight_board.py:181`) is never read: dead code.
- `docs/ARCHITECTURE.md:56` says flights are split "by ground position and ETA". It is great circle / groundspeed, not the ETA model.

## Tree state

- `git status --short` in `X:/dev/vatsim_control_recs.wt/dbg-arr-predicate`: empty (checked after cleanup).
- Throwaway files removed: `.tmp/arr_probe_common.py`, `.tmp/arr_probe_counts.py`, `.tmp/arr_probe_detail.py`, `.tmp/arr_probe_timing.py`, `.tmp/arr_probe_timing2.py`, `.tmp/test_arrival_window.py`, and the logs `.tmp/arr_counts.log`, `.tmp/arr_detail.log`, `.tmp/arr_timing.log`, `.tmp/arr_timing2.log`, `.tmp/arr_red.log`.
- The worktree `.venv` that `uv run` created (gitignored) is removed.
- No runtime was launched beyond the probe processes, which exited.

## Lessons for the project's docs

- `docs/performance.md` "Measuring": calculate_eta costs about 20 us per flight with a warm route cache and about 175 us cold. A process's first pass pays about 1.2 s for the NASR/CIFP indexes. The great-circle check is about 1.3 us.
- `docs/ARCHITECTURE.md` integration footguns: the flight board calls `get_airport_flight_details` with window 0 and lists every inbound. Only the overview applies `max_eta_hours`, and `load_airport_data`'s dict has no elevation, unlike `UNIFIED_AIRPORT_DATA`.
- Test conventions (`docs/ARCHITECTURE.md` test locations, or a testing note): ETA fixtures keep `route` empty. A non-empty route makes `calculate_eta` load NASR/CIFP, which under the conftest's isolated `LOCALAPPDATA` would try to download.
