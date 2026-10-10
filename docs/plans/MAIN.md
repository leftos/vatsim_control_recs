# Plan

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

- [ ] Capture feature screenshots for the leftos.dev showcase page and send them back (leftos.dev inbox request 20261010-081555): shots 01–09 into `X:\dev\leftos.dev\.tmp\vcr-shots\` (overview, flight board, weather briefing, flight info, diversion finder, Go To, Custom Groupings, optional historical stats and VFR alternatives), then an inbox request to `leftos.dev` with the file list, a caption per shot and corrections to its list (which follows `USER_GUIDE.md`).

## Backlog

- [ ] Escape the Go To multi-select marker: `ui/modals/goto_modal.py:406-408` builds `"[x] ..."` in an `OptionList` prompt, which Textual parses as markup, so selected items show no checkbox.

- [ ] Add `docs/README.md` as the docs' start page, linking `docs/ARCHITECTURE.md`, `docs/atis-filtering-and-runway-extraction.md`, `USER_GUIDE.md`, `CLAUDE.md` and this plan: the repo has no docs index, so the architecture entry point is reachable only from `CLAUDE.md` (line 83).
