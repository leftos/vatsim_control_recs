# Go To modal: UX review

Scope: `ui/modals/goto_modal.py` (working tree, which includes the uncommitted `Content(...)` checkbox-markup fix), `ui/modals/save_grouping.py`, `ui/modals/tracked_airports.py`, `backend/core/groupings.py`, the Go To parts of `ui/app.py`, `ui/modals/help_modal.py`, `ui/modals/command_palette.py`, `README.md`, `USER_GUIDE.md` and `CLAUDE.md`. Textual is 8.2.8 (`.venv`).

Everything below comes from reading code, not from driving the app; the one runtime image is `X:\dev\leftos.dev\.tmp\vcr-shots\06-go-to.png`.

Measured: the list holds 263 multi-airport preset groupings plus 5 in `data/custom_groupings.json`, plus the ARTCC "All" groupings and favorites (`.tmp/goto-ux/count_groupings.py`). The list shows at most 100 rows (`goto_modal.py:440`).

## 1. Inventory

"On screen" means the modal itself shows it, in the title, the placeholder, the hint line or a row label. "Must know" means nothing in the modal reveals it.

### Modes and states

| Mode / state | Where | On screen? |
|---|---|---|
| Browse (single-select): Enter opens the highlighted row | `goto_modal.py:145`, `963-1000` | Implicitly. The title stays "Go To" in every mode (`175`) |
| Multi-select, toggled by Tab | `199-205`, `145` | Only through `[ ]`/`[x]` row prefixes and the swapped hint (`733-739`). There is no mode badge, and the title does not change |
| Edit-favorite: multi-select preloaded from a ★ favorite | `651-695`, `_editing_favorite_name` `155` | No. Nothing says "editing X". The only trace is the name prefilled in the save dialog later (`851`) |
| Picker (callback) mode, used by Weather Briefing with no board open | `app.py:1122-1126`, `goto_modal.py:128-131`, `981-993` | The title changes. The hint is still `HINT_SINGLE_SELECT` (`170-172`), which advertises Tab, E and Ctrl+D, but Tab and E are disabled there (`186-188`, `230`) and Ctrl+D is not (`223`) |
| Groupings-only (`filter_type="$"`) | `127`, `167-169`, `296-301`, `341`, `353` | Placeholder and hint change, but **no caller passes it** (only `app.py:1122` and `1285` construct `GoToScreen`). This is dead code |
| Loading | `178`, `426` | "Loading..." row |
| No results | `434-436` | "No results found" row |
| Selection summary | `177`, `697-731` | "Selected: A, B, C + N more (X airports total)", truncated after 3 names (`722-726`). It is still shown after Tab back to browse (it is not cleared) |
| Focus: input versus list | `195-196` | Not shown except by the list border and highlight. **E, F and Space work only when the list has focus** (`230`, `237`, `244`), and focus starts in the input (`183`) |
| 100-row cap | `440`, `461` | No. Rows past 100 are dropped with no "N more" marker |
| Flights hidden in multi-select | `431-432` | No |
| Selected grouping expands into indented member rows | `452-466`, label `374-381` | Yes (rows appear), but they count against the 100-row cap, so selecting "ZOA All" can push every other result off the list |
| After save: back to browse, search box prefilled with the new name | `895-906` | Yes (visible), unexplained |

### Keys

| Key | Mode / focus | Effect | Where | On screen? |
|---|---|---|---|---|
| Typing | input | Substring filter over ICAO+name (airports), name (groupings), callsign+dep+arr (flights) | `482-527`, `509-515` | Placeholder hints at the prefixes only |
| `@` / `#` / `$` prefix | input | Restrict to airports / flights / groupings | `485-493` | Placeholder (`171`) and browse hint (`37-38`) |
| Down / Up from input | input | Focus the list at the top / bottom row | `250-261` | No |
| Up on first row / Down on last row | list | Return focus to the input | `263-272` | No |
| Enter | browse, input or list | Open board (airport, grouping), Flight Info (flight) | `529-543`, `995-1000` | Browse hint |
| Enter | multi-select, input | Toggle the **first** result (highlight resets to 0 on each list rebuild, `468-469`) | `531-532` | Multi hint "Enter/Space Toggle" |
| Space | multi-select, list only | Toggle highlighted | `244-248` | Hint, but not that it needs list focus; in the input it types a space |
| Tab | any (except picker mode) | Toggle multi-select; keeps selections | `199-205` | Both hints |
| Ctrl+Enter (or Ctrl+J) | multi-select | Open one board for all picks, with filters | `209-213`, `769-818` | "Ctrl+Enter Open". Ctrl+J is not mentioned; the comment at `208` says terminals vary |
| F | multi-select, list only, selected row or member row | Cycle both -> dep -> arr | `237-241`, `584-649` | "F Filter". Silent no-op on an unselected row (`613-615`) |
| Ctrl+S | multi-select | Save picks via `SaveGroupingModal` | `216-220`, `820-853` | Multi hint. Silent no-op with no picks (`822-823`) |
| E | browse or multi, list only, ★ row | Load favorite into multi-select for editing | `230-234`, `651-695` | Browse hint "E Edit ★". Silent no-op on a non-★ row (`658-659`) |
| Ctrl+D | any mode, any focus, ★ row | Confirm, then delete favorite | `223-227`, `923-961` | Both hints. Silent no-op on a non-★ row (`930-931`) |
| Esc | any | Close; discards picks and edits without asking | `114`, `1056-1060` | Both hints |
| Mouse click on a row | any | Same as Enter on that row | `536-543` | No |
| App priority shortcuts (Ctrl+G/L, Ctrl+T, Ctrl+E, Ctrl+W, Ctrl+A, Ctrl+B, `?`/F1 when the list has focus) | any | Open their own screens on top of Go To. Priority bindings are resolved App-first across the whole chain (`textual/app.py:3976-3980`); `app.py:404-410` blocks only Ctrl+S | `app.py:150-174` | No |

### Row labels

| Label | Meaning | Where |
|---|---|---|
| `@ KSFO - San Francisco Intl` | Tracked airport | `385` |
| `$ Bay Area (5 airports)` | Preset, ARTCC or custom grouping | `386-392` |
| `★ Name (n airports)` | User favorite (`favorites.json`) | `391` |
| `# UAL1 - KSFO -> KEWR` | Flight | `393-397` |
| `[ ]` / `[x]` / `[x D▸]` / `[x ◂A]` | Multi-select checkbox and filter | `401-411`, `47-51` |
| `     [  ] @ KSFO - ...` | Member row of a selected grouping, with its own filter | `374-381` |

### Adjacent screens

| Screen | Keys | Where |
|---|---|---|
| Save as Favorite | Enter save, Esc cancel; silently overwrites an existing name | `save_grouping.py:56-59`, `125-131` |
| Confirm delete | Y/Enter yes, N/Esc no | `confirm_modal.py:40-43` |
| Tracked Airports Manager | A add, Delete remove picked, S save **whole tracked list**, Enter toggles a row; Space does nothing | `tracked_airports.py:72-77`, `236-244`; `ListView` binds only Enter/Up/Down (`textual/widgets/_list_view.py:64-66`) |

## 2. Task walkthroughs

Step counts include each key press or typed token, and assume the targets are tracked airports.

**(a) Jump to one airport's board.** `Ctrl+G`, type `sfo`, `Enter`: 3 steps. The airport sorts first because airports precede groupings and flights (`app.py:334-348`).
- Lost: only **tracked** airports are listed (`app.py:326`, `goto_modal.py:342`). An untracked airport returns only its flights. With no `--airports/--countries/--groupings` the allowlist is empty (`main.py:453`), so `@` finds nothing at all. USER_GUIDE.md:360 says "Type airport ICAO or name" with no caveat.

**(b) Open one board for several airports.** `Ctrl+G`, `Tab`, then per airport: type, `Enter` (toggles the top hit), clear the box; then `Ctrl+Enter`. For 3 airports that is 2 + 3x3 + 1 = 12 steps.
- Lost: Tab reads as "move focus", and in the main view it switches tabs (README.md:114), so multi-select is easy to miss. The only cue is the checkboxes.
- Space, the toggle key the hint lists first, types a space while the input has focus. You must press Down to reach the list, which nothing says.
- The search box does not clear after a toggle, and picks that don't match the current query disappear from view; the summary truncates after 3 names.
- Ctrl+Enter is unreliable across terminals (`208`); the Ctrl+J fallback is undocumented.

**(c) Save a grouping or favorite from a selection.** From (b): `Ctrl+S`, type a name, `Enter`: +3, so 14 steps.
- Lost: the key says "Save", the dialog says "Save as Favorite" (`save_grouping.py:92`), and the placeholder says "Enter grouping name" (`95`).
- An existing name is overwritten without a prompt (`125-131`). A name equal to a preset grouping silently shadows it everywhere, because favorites have the highest priority (`groupings.py:349-354`).
- Afterwards the modal drops to browse with the name typed in the search box (`895-906`) and no "Saved" message.

**(d) Departures-only or arrivals-only per airport.** `Ctrl+G`, `Tab`, type, `Enter` (pick), `Down` (focus the list), arrow to the row, `F` (dep) or `F F` (arr), then `Ctrl+Enter` or `Ctrl+S`: about 8-9 steps per airport.
- Lost: F works only with list focus *and* on a picked row, and is otherwise silent. F on a grouping row sets every member; F on an indented member row sets one.
- The filter only survives if you save. A saved favorite's filters apply only when it is opened **from Go To** (`goto_modal.py:1036-1041`); Enter on the same grouping in the main Custom Groupings tab ignores them (no `airport_filters` anywhere in `ui/app.py`).
- **Bug (edit mode):** `_edit_favorite` restores filters under `sub:ICAO` keys only (`689-691`). An individually picked airport's row reads `airport:ICAO` (`403-405`), so its saved filter is not drawn on its row. Pressing F changes the row label, but `_resolve_filters` lets `sub:` keys win (`761-765`), so the saved value comes back on save. In edit mode you cannot change an individual airport's filter, and the row shows the wrong value.

**(e) Build a grouping of groupings plus individual airports.** `Ctrl+G`, `Tab`, `$bay`, `Enter`, clear, `$socal`, `Enter`, clear, `ksmf`, `Enter`, `Ctrl+S`, name, `Enter`: about 13 steps. It works, and the saved favorite stores the grouping names, so it stays nested (`826-828`).
- Lost: nothing on screen says groupings and airports can be mixed. Picking a large grouping floods the 100-row cap with member rows (`452-466`).
- **Impossible:** adding an airport that is not tracked. You must leave, track it (`Ctrl+T`, `A`, `+KSMF`, `Enter`, `Esc`, wait for the refresh), then come back.
- **Impossible:** excluding one member of a grouping, because member rows cannot be toggled (`557-558`).
- The Tracked Airports Manager's `S` cannot do this at all: it saves the entire tracked list with no groupings and no filters (`tracked_airports.py:243`), ignoring the rows you picked.

**(f) Edit an existing favorite or grouping: rename, add, remove.** `Ctrl+G`, type the name, `Down` (E needs list focus), `E`. Then per change: type, Down/arrow, `Enter`/`Space`. Then `Ctrl+S`, edit the name to rename, `Enter`: 8+ steps.
- Lost: only ★ favorites can be edited. On a `$` grouping E does nothing and says nothing. Preset and `custom_groupings.json` groupings can only be changed by editing files.
- Nothing says you are editing. Esc throws the edit away without asking.
- Rename is hidden in the save dialog. On rename the old entry is removed, and a failure there is swallowed (`882-883`).
- **Impossible:** removing a member that is an untracked airport. It is loaded into the selection (`685-686`) but never appears in the list, so it cannot be un-picked.

**(g) Delete one.** `Ctrl+G`, type the name, `Ctrl+D`, `Y`: 4 steps (Ctrl+D works from the input because row 0 is highlighted).
- Lost: only ★ favorites, and silent on anything else.
- In the Weather Briefing picker Ctrl+D also deletes favorites (`223`, no mode guard). A file error makes the delete fail silently (`950-951`).

**(h) Find a flight by callsign.** `Ctrl+G`, `#ual12` (or just `ual12`), `Enter`: 3 steps, which opens Flight Info.
- Lost: in multi-select, flights silently vanish (`431-432`), so a user who left Tab on sees "No results found".
- Without `#`, a callsign fragment can also match airport names and grouping names first.

## 3. Problems, ranked by how much they hurt

1. **Hidden mode and focus rules.** Multi-select and edit modes have no title or badge (`175` never changes). E, F and Space need list focus (`230`, `237`, `244`), but focus starts in the input (`183`) and the hint never says so. The two-line hint lists 6-7 keys at once and wraps (screenshot). This is the root of "hard to remember how to navigate".
2. **Building and editing groupings is crammed into a search box.** Members you can't see can't be removed (untracked airports, `685-686` vs `431-466`). Untracked airports can't be added at all (`app.py:326`). The edit-mode filter bug (`689-691` vs `403-405`, `761-765`). Large groupings flood the 100-row cap (`440`, `452-466`). Rename lives in the save dialog. Esc discards edits silently (`1056-1060`).
3. **Silent no-ops and silent destruction.** E, Ctrl+D, F, Ctrl+S and Ctrl+Enter all do nothing, with no message, when their precondition fails (`658-659`, `930-931`, `613-615`, `822-823`, `771-772`). Save overwrites without asking (`save_grouping.py:125-131`) and can shadow a preset grouping (`groupings.py:349-354`). Rename and delete swallow file errors (`882-883`, `950-951`).
4. **Three nouns for one thing.** "Favorite" (`save_grouping.py:92`, Go To hints "★"), "grouping" (placeholder `save_grouping.py:95`, Tracked Airports "Save as Grouping" `tracked_airports.py:76`, `112`), and "custom grouping" (README.md:154, USER_GUIDE.md:586-591, CLAUDE.md:285). All three write the same `favorites.json`.

   Meanwhile "custom groupings" is also the name of a different file (`custom_groupings.json`) and of the main tab that shows all of them. A favorite's filters apply only when opened from Go To, not from that tab.
5. **Overloaded and inconsistent keys.**
   - Tab means "switch tabs" in the main view and "toggle mode" here, and never "move focus".
   - Save is `S` in Tracked Airports and `Ctrl+S` here (where `Ctrl+S` is also the app's Historical Stats, rescued only by `app.py:404-410`). Delete is `Delete` there and `Ctrl+D` here.
   - F means "filter this airport" here and "cycle the whole board" on the flight board (`flight_board.py:161`).
   - Q closes every other list modal (`flight_board.py:160`, `flight_info.py:90`, `weather_briefing.py:146`) but not this one.
   - App priority shortcuts open other screens on top of Go To, including a second Go To on Ctrl+G.
   - In the Weather Briefing picker the hint advertises Tab and E, which do nothing, and Ctrl+D, which deletes (`170-172`, `186-188`, `223`).
6. **Docs disagree with code.**
   - USER_GUIDE.md:616 says favorites appear "at the top"; they sort alphabetically among ~290 groupings after the airports (`app.py:340-342`), so with no query they usually fall past the 100-row cap.
   - USER_GUIDE.md:377-379 and 627-628 show `[D>]`/`[<A]`; the code draws `[x D▸]`/`[x ◂A]` (`goto_modal.py:47-51`, `406-409`).
   - README.md:154, USER_GUIDE.md:586-591 and CLAUDE.md:285 say Tracked Airports `S` saves a "custom grouping"; it writes a favorite (`save_grouping.py:117-136`) of the whole tracked list.
   - README.md:151, USER_GUIDE.md:529, CLAUDE.md (Tracked Airports table) and `tracked_airports.py:117` say Space selects; only Enter does.
   - README.md:132-143 omits Enter, Space and Ctrl+Enter for Go To. `help_modal.py:25` and `command_palette.py:21` omit Ctrl+L and say nothing about Go To's own keys.
   - USER_GUIDE.md:360 omits "tracked only". No doc mentions the list-focus rule, the 100-row cap or flights hiding in multi-select.
7. **Dead and stray paths.** `filter_type="$"` has no caller (`127`, `167-169`, `296-301`). `_open_weather_briefing_callback` handles a `("airports", ...)` result (`app.py:1135`) that Go To never emits, though its title promises "Enter airports" (`app.py:1123`).

## 4. Recommendations

**R1. Make the mode and the keys visible (S; `ui/modals/goto_modal.py`, then README.md, USER_GUIDE.md, CLAUDE.md).**
- Title shows the mode: "Go To", "Go To · Pick several (3 picked, 9 airports)", "Go To · Editing ★ Bay Area".
- The hint line is computed from mode + focus + highlighted row type, one line, 3-5 keys. For example, on a `$` row in browse it shows "Enter open · Tab pick several"; on a ★ row it adds "E edit · Ctrl+D delete".
- Entering multi-select moves focus to the list, and any printable key in the list goes back to the search box (type-to-search), so Space, F and E always mean their action there.
- Every precondition failure gets a `notify()` ("E edits ★ groupings only; 'Bay Area' is a preset"). Save, delete and rename failures get an error toast instead of `pass`.
- Doing this as Textual `Binding`s with `check_action`, instead of the `on_key` chain, gives one source of truth for keys and hints.

**R2. In-modal help overlay (S-M; `goto_modal.py`, `help_modal.py` or a new `goto_help.py`, `app.py` `check_action`).**
- `?`/F1 inside Go To opens a task-oriented cheat sheet ("Open several airports: Tab → Enter on each → Ctrl+Enter"; "Make a grouping: …").
- Needs `app.py:404-410` to stop the app-level `?`/F1 when a `GoToScreen` is on top, because App priority bindings win the chain.
- Block the other app shortcuts there too, at least Ctrl+G/L.

**R3. Split "build or edit a grouping" into its own screen (L).**

Files: new `ui/modals/grouping_builder.py`; `goto_modal.py` loses `_edit_favorite`/`_save_selection`/rename/delete-file code; `save_grouping.py` folded in or removed; `tracked_airports.py` `S` opens the builder prefilled with its picks.

`backend/core/groupings.py` gains `save_user_grouping`/`delete_user_grouping`/`rename_user_grouping`, so the JSON I/O now duplicated at `goto_modal.py:866-873`, `943-949` and `save_grouping.py:117-136` lives in one place.
- A name field, an Add pane that searches **all** airports in the unified data plus groupings, and a Members pane that always lists every member (tracked or not) with its filter.
- Del removes a member; members of a nested grouping can be excluded.
- Ask before overwriting an existing name or shadowing a preset; ask before Esc discards changes.
- "Copy to my groupings" on a preset row gives users a way to customise presets without editing files.
- Go To keeps search, open, and quick multi-open (Tab → picks → Ctrl+Enter), with "Ctrl+S save as grouping" handing the picks to the builder.
- Fixes problems 2 and 3 and the edit-mode filter bug by construction.

**R4. One noun, and filters that follow the grouping (S-M; `save_grouping.py`, `goto_modal.py`, `tracked_airports.py`, `ui/app.py` flight-board open path, README.md, USER_GUIDE.md, CLAUDE.md, `help_modal.py`).**
- Call the user's saved sets "my groupings" (★) everywhere and drop "favorite" from the UI; keep `favorites.json` as the file name.
- Apply a ★ grouping's saved dep/arr filters wherever it is opened, including Enter in the main groupings tab.

**R5. List hygiene (S; `ui/app.py` `_build_goto_results`, `goto_modal.py`).**
- ★ groupings first, then airports, groupings, flights, with section headers.
- A "showing 100 of 412 — type to narrow" row when capped.
- Selected groupings show a collapsed "▸ 8 airports" row that expands on → instead of always inlining members.
- Keep flights visible but dimmed in multi-select, with "flights can't be picked" on Space.
- Delete the dead `filter_type="$"` path.

**Do first: R1.** It is one file and small, and it targets the lead dev's actual complaint, not remembering how to drive the modal: every mode, focus rule and no-op becomes visible on screen. It also does not lock in anything R3 would change. R3 is the larger win for task (e) and (f) and should come next, since it removes the edit mode that R1 would otherwise have to label. Fix the edit-mode filter bug (problem 2) on its own in the meantime; it is a data-correctness bug, not a UX preference.

Backlog candidates for the plan (pre-existing defects found here):
- Edit-mode filter keys (`goto_modal.py:689-691` vs `403-405`, `761-765`).
- Tracked Airports Space hint versus Enter-only `ListView` (`tracked_airports.py:117`) and `S` ignoring picks (`243`).
- Ctrl+D active in the Weather Briefing picker (`goto_modal.py:223`).
- Favorite filters ignored from the Custom Groupings tab.
- Unhandled `"airports"` branch in `app.py:1135`.
- Dead `filter_type`.

## 5. Mockups

Browse mode (R1 + R5). The hint is computed for the highlighted row; here it is a ★ grouping.

```
┌──────────────────────────────── Go To ────────────────────────────────┐
│ > bay_                                          @ airport # flight $ grp│
│                                                                        │
│  ★ MY GROUPINGS                                                        │
│ ▌★ Bay Area Arrivals             5 airports   2 dep · 3 arr           │
│  AIRPORTS (tracked only)                                               │
│   @ KSFO  San Francisco Intl                                           │
│  GROUPINGS                                                             │
│   $ Bay Area                     5 airports                            │
│   $ ZOA Bay TRACON              14 airports                            │
│  FLIGHTS                                                               │
│   # BAY12  KOAK → KLAS                                                 │
│                                                                        │
│ Enter open · Tab pick several · E edit · Ctrl+D delete · ? help        │
└────────────────────────────────────────────────────────────────────────┘
```

Multi-select mode (R1 + R5): mode in the title, all picks listed, focus on the list, typing goes to search.

```
┌──────────── Go To · PICK SEVERAL · 3 picked → 11 airports ────────────┐
│ > london_                                                              │
│ Picked: $London TMA (8) · EGKK dep · EGGW                               │
│                                                                        │
│ ▌[x] $ London TMA               8 airports  both      ▸ → members      │
│  [x] @ EGKK  London Gatwick                 dep only                   │
│  [x] @ EGGW  London Luton                   both                       │
│  [ ] @ EGLL  London Heathrow                                           │
│  [ ] @ EGSS  London Stansted                                           │
│   ·  # flights hidden while picking                                    │
│                                                                        │
│ Space pick · F dep/arr/both · Ctrl+Enter open board · Ctrl+S save…     │
│ Tab back to browse (keeps picks) · Esc close                           │
└────────────────────────────────────────────────────────────────────────┘
```

Grouping builder (R3), opened by Ctrl+S from picks, E on a ★ row, or `S` in Tracked Airports.

```
┌─────────────────────── Edit grouping ★ London Ops ─────────────────────┐
│ Name: [London Ops______________]          was: London Ops (rename ok)   │
├──────────── Add ─────────────────┬──────── Members · 4 → 12 airports ──┤
│ > egs_                           │ ▌$ London TMA     8 apts   both      │
│ ▌@ EGSS London Stansted          │    ↳ EGLL excluded            (x)    │
│  @ EGSC Cambridge   (untracked)  │  @ EGKK Gatwick            dep       │
│  $ Stansted area    (preset)     │  @ EGGW Luton              both      │
│                                  │  @ EGSC Cambridge (untrk)  arr       │
│ Enter add →                      │ F dep/arr/both · Del remove · → expand│
├──────────────────────────────────┴──────────────────────────────────────┤
│ Tab switch pane · Ctrl+S save · Esc cancel (asks if changed)             │
└──────────────────────────────────────────────────────────────────────────┘
```
