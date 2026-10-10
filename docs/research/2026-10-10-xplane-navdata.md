# X-Plane navdata as a worldwide source of route idents

Research date: 2026-10-10. Every source below was read on that date. Facts marked **unverified** could not be confirmed from a primary page in this pass.

## Question

Can X-Plane's navdata (fixes, navaids, airways, SIDs/STARs) serve as a worldwide source of route idents for parsing filed routes, so that non-US routes get route distance for ETAs, route weather and MEA checks?

## Context

`backend/data/navaids.py` downloads FAA NASR `NAV.zip`, `FIX.zip` and `AWY.zip` from `https://nfdc.faa.gov/webContent/28DaySub/<date>/` once per 28-day cycle and parses the fixed-width `.txt` files inside. `backend/data/cifp.py` downloads `CIFP_<yymmdd>.zip` from `https://aeronav.faa.gov/Upload_313-d/cifp/` and parses the single `FAACIFP18` file (ARINC 424). Both cover only the US and its territories, which is why `is_us_airport` (`backend/core/calculations.py`) keeps every route with a non-US end on great-circle distance.

## 1. Default X-Plane 12 navdata: supplier and licence

- **Files and cycle.** "X-Plane 11/12 ships with a global base layer of data that enables IFR navigation world-wide. The data cycle represented by those files will remain the same over the lifetime of X-Plane 12. These files are: earth_fix.dat, earth_awy.dat, earth_nav.dat, earth_hold.dat, earth_mora.dat, earth_msa.dat, CIFP/#ICAO.dat", located in `$X-Plane/Resources/default data/` ([Navdata in X-Plane 11 and 12](https://developer.x-plane.com/article/navdata-in-x-plane-11/), updated 5 June 2026). So the default set is a frozen cycle, not AIRAC-current.
- **Source and copyright are in the file header.** "Refer to the first line of each data file (such as earth_nav.dat) for information about the source and copyright" ([Data Development Documentation](https://developer.x-plane.com/docs/data-development-documentation/)). The header of the shipped default files was not read in this pass (**unverified**). A Navigraph staff post quotes the header of Navigraph's own X-Plane 12 files as `1200 Version - data cycle 2405, build 20240504, metadata FixXP1200. Copyright (c) 2024 Navigraph, Datasource Jeppesen` ([Navigraph forum](https://forum.navigraph.com/t/crash-to-disk-in-x-plane-12-because-of-navigraph/15743)); that the default files carry the same supplier is **unverified** from a header, but the EULA below says so.
- **Licence: Jeppesen data via Navigraph, no redistribution.** The X-Plane 12 EULA carries Jeppesen's additional terms: "YOU ... HAVE LICENSED JEPPESEN'S NAVDATA ("DATA") ... FROM NAVIGRAPH KB ("OEM")" ([copy of the text](http://data.gpo.zugaina.org/flightsim/licenses/X-Plane)), and in the [X-Plane 12 EULA on Steam](https://store.steampowered.com/eula/2014780_eula_0): "2. OWNERSHIP/COPYRIGHT. Jeppesen is the owner or authorized licensor of the Products, which is protected by copyright." and "3. RESTRICTIONS ON USE. The Products may not be used for any other purpose, nor be sold or given by End-User to any third party for any use. Use of the Data by End-User to generate aviation charts or maps, whether for reference only or actual air navigation, is expressly prohibited. ... End-User will NOT: (a) copy, reproduce, reverse engineer, decompile, disassemble or publish the Data; (b) electronically transfer the Products to multiple computers over a network system; (c) distribute copies of the Products or accompanying materials to others; (d) modify, adapt, or translate the Products or create derivative works based on the Products; or (e) assign, rent, lend, sell, transfer or sublease the Products."
- **Navigraph subscription data** (the AIRAC-current replacement in `Custom Data/`) is licensed per subscriber: "a personal, revocable, non-exclusive, non-transferable, non-assignable, time-limited, worldwide license to use the Data without the right to copy, modify, publish, sub-license, disseminate and otherwise exploit the Data" (Navigraph EULA §3.1, quoted in a [FlightGear forum post](https://forum.flightgear.org/viewtopic.php?p=358094); the [Navigraph terms page](https://navigraph.com/legal/terms-of-service) did not render, so the exact current wording is **unverified**). Navigraph's older-format header reads: "This data may be used for ground based recreational computer game simulation software only, and may not be recompiled, interpreted, or distributed for any purpose without the written consent of Navigraph" ([Navigraph forum](https://forum.navigraph.com/t/crash-to-disk-in-x-plane-12-because-of-navigraph/15743)).
- **What that means for a third-party app.** Shipping the files, or hosting them for the app to download, is "distribute copies ... to others": not allowed. No Laminar or Navigraph download URL for the default files exists; they come only inside an X-Plane install. Reading them from a user's own X-Plane install at runtime would be the user's own copy, but the licence limits use to the simulator ("may not be used for any other purpose") and forbids derivative works, so even that is outside the licence as written. Whether Laminar or Navigraph would grant written consent is **unverified** (not asked).

## 2. The older community data (Robin Peel, data.x-plane.com)

- **What it is.** Robin A. Peel's worldwide `earth_nav.dat`, `earth_fix.dat`, `earth_awy.dat` and `apt.dat`, built originally from DAFIF (the US NIMA Defense Aeronautical Flight Information File) and published at `http://data.x-plane.com` ([XP NAV810 spec](http://developer.x-plane.com/wp-content/uploads/2015/11/XP-NAV810-Spec.pdf)).
- **Licence: GPL v2 or later.** Header text: "This data is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later version." The DAFIF acknowledgement and NIMA warranty statements form part of the header, and "The terms of this license require that this copyright message must be left intact if this file is modified and/or redistributed" ([XP NAV740 definition](https://developer.x-plane.com/article/x-plane-nav-aid-data-file-definition-740-version/)). The 810 spec: "The GNU GPL ... under which this data is released is designed to encourage modifications, enhancements and redistribution, even in commercial derivative products" ([NAV810 article](https://developer.x-plane.com/article/navigation-data-nav-dat-earth_nav-dat-file-format-specification/)).
- **Last update.** The GPL files were published until the XP11 format change; an `earth_awy.dat` header seen by a FlightGear developer reads "640 Version - data cycle 2013.10 ... Copyright ©2013, Robin A. Peel ... GNU General Public License" ([flightgear-devel thread](https://sourceforge.net/p/flightgear/mailman/flightgear-devel/thread/1bfbb5a6-db5c-f702-c6fc-bcbd0390dcbb@gmail.com/)). FlightGear's copy "was last updated around 2012" and "The sources we used back then are not GPL compliant anymore" ([FlightGear forum](https://forum.flightgear.org/viewtopic.php?p=358094)). The exact last GPL cycle is **unverified**; roughly 2013.
- **Download.** `data.x-plane.com` no longer serves the files: "all other links to downloadable files seem to have disappeared" ([flightgear-devel thread](https://sourceforge.net/p/flightgear/mailman/flightgear-devel/thread/1bfbb5a6-db5c-f702-c6fc-bcbd0390dcbb@gmail.com/)). Copies survive in FlightGear's data repository (`Navaids/nav.dat.gz`, `fix.dat.gz`, `awy.dat.gz`; path **unverified**).
- **Usable as a current source?** No. Thirteen years of airway, fix and SID/STAR change (RNAV route restructuring in Europe and elsewhere) make it wrong for current VATSIM flight plans, it has no procedures, and DAFIF itself stopped public release in 2006 (**unverified** date). The GPL also means a bundled copy must keep the header intact and ship under GPL terms.

## 3. Where each is published and how often it updates

| Source | Where | Stable URL an app can fetch | Update rate |
|---|---|---|---|
| X-Plane 12 default navdata | Inside the X-Plane install, `Resources/default data/` ([article](https://developer.x-plane.com/article/navdata-in-x-plane-11/)) | None | Frozen "over the lifetime of X-Plane 12" |
| Navigraph X-Plane 12 data | Navigraph subscription, FMS Data Manager or manual download after login ([Navigraph forum](https://forum.navigraph.com/t/crash-to-disk-in-x-plane-12-because-of-navigraph/15743)) | None (account-bound) | Every 28-day AIRAC cycle |
| Aerosoft NavDataPro | Subscription ([article](https://developer.x-plane.com/article/navdata-in-x-plane-11/)) | None | Every AIRAC cycle (**unverified**) |
| Robin Peel GPL data | Formerly `http://data.x-plane.com`; copies in FlightGear | None current | Stopped about 2013 |
| Scenery Gateway | Airports (`apt.dat`) only, not navdata ([Data Development Documentation](https://developer.x-plane.com/docs/data-development-documentation/)) | Gateway API (not researched) | Continuous |

## 4. Formats

X-Plane 11 and 12 load either an ARINC 424 file or the XP text formats ([article](https://developer.x-plane.com/article/navdata-in-x-plane-11/)):

- **ARINC 424 override.** `Custom Data/earth_424.dat`, "interpreted according to the ARINC 424.18 standard with the FAA CIFP exceptions"; supported: "ARINC 424 supplements 18 up to 20, with the exceptions noted in the FAA/Aeronautical Information Services CIFP Readme". It carries fixes (EA, PC, HC), navaids (D, DB, PN), airways (ER), holds, MSA, MORA, procedures (PD, PE, PF) and more. This is the same layout as FAA CIFP, so `cifp.py`'s parser would read such a file, but nobody publishes a free worldwide one.
- **XP text formats** (whitespace-separated, header line `I` then `<version> Version - data cycle <AIRAC>, build <date>, metadata ...`, terminated by `99`). Spec PDFs:
  - [XP FIX1200](https://developer.x-plane.com/wp-content/uploads/2021/09/XP-FIX1200-Spec.pdf) (X-Plane 12; adds a waypoint name column) and [XP FIX1101](http://developer.x-plane.com/wp-content/uploads/2019/01/XP-FIX1101-Spec.pdf). Each fix is keyed by ident, terminal area or `ENRT`, and ICAO region code.
  - [XP NAV1200](http://developer.x-plane.com/wp-content/uploads/2016/10/XP-NAV1200-Spec-2.pdf) and [XP NAV1150](http://developer.x-plane.com/wp-content/uploads/2020/03/XP-NAV1150-Spec.pdf).
  - [XP AWY1101](http://developer.x-plane.com/wp-content/uploads/2019/01/XP-AWY1101-Spec.pdf) (the article lists it as XPAWY1100).
  - [XP HOLD1140](http://developer.x-plane.com/wp-content/uploads/2018/12/XP-HOLD1140-Spec.pdf), [XP MORA1150](http://developer.x-plane.com/wp-content/uploads/2020/03/XP-MORA1150-Spec.pdf), [XP MSA1150](http://developer.x-plane.com/wp-content/uploads/2020/03/XP-MSA1150-Spec.pdf).
  - [XP CIFP1250](http://developer.x-plane.com/wp-content/uploads/2016/10/XP-CIFP1250-Spec.pdf) and [XP CIFP1101](http://developer.x-plane.com/wp-content/uploads/2019/01/XP-CIFP1101-Spec.pdf).
- **The per-airport `CIFP/<ICAO>.dat` files are not ARINC 424 column layout.** The CIFP1250 spec: "The format is a simplified, reorganized, slightly abbreviated representation of ARINC 424.18+ records." Rows start with a row code (`SID`, `STAR`, `APPCH`, `RWY`, `PRDAT`, and helicopter `HSID`/`HSTAR`/`HAPPCH`/`HPRDAT` from X-Plane 12.5), "Row codes are separated from the payload by a colon", "Fields are separated by the comma", "Lines are terminated by the semicolon". Each comma field maps to an ARINC 424.20 column range (for example SID/STAR/APPCH field 1 = columns 27-29, the sequence number). A new parser would be needed; field semantics match what `cifp.py` already decodes.
- **Global uniqueness.** "All fixes and navaids must specify the region ICAO code ... each item in the database is addressable by a globally unique identifier", and terminal items carry their airport ident ([article](https://developer.x-plane.com/article/navdata-in-x-plane-11/)). The key is (ident, region, terminal area), unlike NASR's US-only ident space.

## 5. Other free worldwide sources

- **OurAirports `navaids.csv`**: public domain ("All data is released to the Public Domain"), nightly, worldwide VOR/NDB/DME/TACAN only, no fixes, airways or procedures; stable URL via the [davidmegginson/ourairports-data](https://github.com/davidmegginson/ourairports-data) repo ([OurAirports data](https://ourairports.com/data/)).
- **openAIP**: CC BY-NC 4.0 ([home](https://www.openaip.net/), [legal](https://www.openaip.net/legal)); daily exports at `https://storage.openaip.net/openaip-system-exports/` without auth ([docs](https://www.openaip.net/docs)); navaids, VFR reporting points, airspace, airports, community-maintained; no IFR waypoints, airways or SIDs/STARs (**unverified** beyond the reporting-points page).
- **FAA CIFP / NASR**: public domain, US and territories only (already used).
- **Eurocontrol** (EAD Basic, NM B2B): free registration, ECAC area, terms restrict redistribution (**unverified**, not read).
- **National AIPs' AIXM/eAIP datasets** (for example the UK NATS AIRAC data, Canada's, Australia's): free per country, licences vary per state (**unverified**, not read).
- **VATSIM community data** (for example vATIS/sector files, `vatspy-data-project`): covers FIRs and airports, not fixes or airways (**unverified**).

## What this means for the app

Options, without a choice:

1. **Do not use X-Plane navdata.** The default and Navigraph files are Jeppesen data under terms that forbid copying, redistribution, derivative works and non-simulator use; the app can neither ship nor download them. Keep NASR + the `is_us_airport` gate.
2. **Read a user-supplied X-Plane install (opt-in path setting).** Technically simple (whitespace text formats, specs above), no redistribution by the app, but outside the licence's permitted use as written; would need Navigraph's or Laminar's written consent to be clean.
3. **GPL Robin Peel data.** Legally redistributable, but about 2013 and missing procedures: wrong idents for many current routes.
4. **OurAirports navaids (public domain) as a partial worldwide layer.** Resolves VOR/NDB idents only; routes made of RNAV fixes and airways still fail, so a parsed route would need a "partial resolution" rule.
5. **Per-country open AIP datasets.** Correct and current where available, but one parser and licence check per state.

Open design questions for the owner, whichever source is chosen:

- **Merge rule with NASR**: does a US ident keep its NASR position when the worldwide source also has it, and how is an ambiguous ident resolved (nearest to the previous route point, or the ICAO region of the flight-plan ends)?
- **The `is_us_airport` gate**: can it go entirely, or does it become "both ends inside the coverage of the loaded sources", with the 2x / 100 nm sanity check kept as the guard against wrong-ident resolution?
- **Partial resolution**: is a route with unresolved idents still used for distance, or does it fall back to great circle?

## References

- [Navdata in X-Plane 11 and 12](https://developer.x-plane.com/article/navdata-in-x-plane-11/) - base layer, override layers, formats, spec links
- [Data Development Documentation](https://developer.x-plane.com/docs/data-development-documentation/) - header carries source and copyright
- [X-Plane 12 EULA (Steam)](https://store.steampowered.com/eula/2014780_eula_0) - Jeppesen additional terms
- [X-Plane licence text copy](http://data.gpo.zugaina.org/flightsim/licenses/X-Plane) - "licensed Jeppesen's NavData ... from Navigraph KB"
- [Navigraph forum: XP12 headers](https://forum.navigraph.com/t/crash-to-disk-in-x-plane-12-because-of-navigraph/15743)
- [XP CIFP1250 spec](http://developer.x-plane.com/wp-content/uploads/2016/10/XP-CIFP1250-Spec.pdf), [XP FIX1200](https://developer.x-plane.com/wp-content/uploads/2021/09/XP-FIX1200-Spec.pdf), [XP NAV1200](http://developer.x-plane.com/wp-content/uploads/2016/10/XP-NAV1200-Spec-2.pdf), [XP AWY1101](http://developer.x-plane.com/wp-content/uploads/2019/01/XP-AWY1101-Spec.pdf)
- [XP NAV740 definition](https://developer.x-plane.com/article/x-plane-nav-aid-data-file-definition-740-version/), [XP NAV810 article](https://developer.x-plane.com/article/navigation-data-nav-dat-earth_nav-dat-file-format-specification/) - GPL licence text
- [flightgear-devel thread](https://sourceforge.net/p/flightgear/mailman/flightgear-devel/thread/1bfbb5a6-db5c-f702-c6fc-bcbd0390dcbb@gmail.com/), [FlightGear forum](https://forum.flightgear.org/viewtopic.php?p=358094) - end of the GPL data
- [OurAirports data](https://ourairports.com/data/), [openAIP docs](https://www.openaip.net/docs), [openAIP legal](https://www.openaip.net/legal)
