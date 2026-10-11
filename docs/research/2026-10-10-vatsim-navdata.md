# Worldwide navdata from VATSIM, pilot-side and national sources

Research date: 2026-10-10. Every source below was read on that date. Facts marked **unverified** could not be confirmed from a primary page in this pass. Follows [2026-10-10-xplane-navdata.md](2026-10-10-xplane-navdata.md), which rules out X-Plane's navdata and covers OurAirports navaids and openAIP; those are not repeated here.

## Question

Is there navdata the app may legally download at runtime or ship, covering fixes, navaids and airways (ideally SIDs/STARs) outside the US, so that filed non-US routes can be parsed? The current source is FAA NASR + FAA CIFP, US only.

## 1. Data VATSIM controller clients ship or fetch

### VATSIM x Navigraph partnership and AeroNav

- **The deal.** VATSIM announced on 4 October 2025: "Navigraph will provide Jeppesen-powered Navigation Data, updated every AIRAC cycle, to support VATSIM's global network of virtual pilots and air traffic controllers. AeroNav plays a crucial role by running the service that converts data into sector files used by VATSIM controllers" ([VATSIM forum announcement](https://forum.vatsim.net/t/partnership-announcement-vatsim-x-navigraph/10185)). The announcement names sector files and EuroScope; it says nothing about third-party tools, and its terms are not public (**unverified** whether any third-party use is covered).
- **Before the deal.** VATSIM UK: "as of early 2025, this data is no longer freely available to VATSIM UK", and controllers were told to overwrite `AIRWAY.txt` and `ISEC.txt` from their own Navigraph subscription ([VATSIM UK NavData Update Guide](https://docs.vatsim.uk/General/Use%20of%20Software/NavData%20Update%20Guide/)). So the navdata inside controller packs is licensed Navigraph data, not open data.
- **AeroNav GNG.** The Global Nav Generator builds EuroScope/VRC sector files; "a lot of data is taken automatically from Navigraph's AIRAC cycle" ([GNG about](https://gng.aero-nav.com/AERONAV/about)). AeroNav is an association domiciled in Lucerne. Its agreement page says: "You are not allowed to upload any of our files anywhere. GNG/AeroNav files are exclusively hosted on the AeroNav Association server", "You are not allowed to decompile or use any data outside the controller client without the explicit agreement from the data owner", and "The provided data must be used for the personal, flight-simulation use on the PC only". It also lets AeroNav "share your data with other applications (e.g. atc clients, pilot clients, observer clients) which are in connection with the VATSIM-Network ... AeroNav will provide a list of all tools that use AeroNav GNG data" ([GNG agreement](https://f.dev.aero-nav.com/AERONAV/agreement); this copy is on AeroNav's dev host, the production page was not read, **unverified** that it matches).
- **Downloads.** Per-vACC AIRAC packages at [files.aero-nav.com](https://files.aero-nav.com/): about 110 FIRs/vACCs across Europe, Africa, the Middle East, Russia/Central Asia, South America, parts of Asia, four Canadian FIRs and ZOA. Not worldwide (no China, Japan, most of South-East Asia, Australia). Format: EuroScope `.sct`/`.ese` (fixes as `[FIXES]`, `[VOR]`, `[NDB]`, `[LOW AIRWAY]`/`[HIGH AIRWAY]` line geometry, SIDs/STARs as `SID:`/`STAR:` lines in `.ese`; layout **unverified** in this pass). Updated every AIRAC cycle.
- **Fetch/ship.** Not allowed without explicit agreement: use outside the controller client is forbidden and rehosting is forbidden. The "list of tools" clause suggests AeroNav can approve a VATSIM-connected tool, but the data owner is Navigraph/Jeppesen, so approval would need both (**unverified**, not asked).

### vatSys profiles

- vatSys datasets live on GitHub ([vatSys/australia-dataset](https://github.com/vatSys/australia-dataset), plus pacific, New Zealand, UK, Algeria and others under [github.com/vatSys](https://github.com/vatSys)). The repo has no licence file (`gh api` reports `license: null`), so default copyright applies: no redistribution right.
- They do not carry the worldwide navdata. "The definition of all WAYPOINT, NAVAID, AIRPORT, SID & STAR names referenced by vatSys XML files are searched for in the following order: 1. Airspace.xml 2. Navigraph Data" ([Data Preparation Kit](https://virtualairtrafficsystem.com/docs/dpk/)). Navigraph publishes a "vatSys" navdata package each cycle ([Navigraph forum](https://forum.navigraph.com/t/incorrect-cycle-info-for-vatsys-navdata-on-airac-2207/8610)), subscription-bound. `Airspace.xml` holds only local overrides (`<Intersections>`, `<Airways>`) for a few FIRs.
- **Fetch/ship.** No: unlicensed repos for the local part, Navigraph subscription data for the rest.

### vatSpy data project

- [vatsimnetwork/vatspy-data-project](https://github.com/vatsimnetwork/vatspy-data-project): `VATSpy.dat` (sections `[Countries]`, `[Airports]`, `[FIRs]`, `[UIRs]`, `[IDL]`) and `Boundaries.geojson`. Licence CC BY-SA 4.0 (`LICENSE` file). Updated per AIRAC by approved contributors; last push 2026-10-04. Raw URLs on `raw.githubusercontent.com` are stable.
- **No fixes, navaids or airways.** Useful only for airport positions and FIR polygons (the app already uses SimAware boundaries, also CC BY-SA 4.0: [simaware-tracon-project](https://github.com/vatsimnetwork/simaware-tracon-project)).

### VATSIM-endorsed open navdata

None found. VATSIM's own pilot docs point to Navigraph as "a payware resource for viewing charts worldwide and updating AIRAC data" ([VATSIM pilot docs](https://vatsim.net/docs/pilots/pilots/)). The partnership above is the network's navdata arrangement.

## 2. Pilot-side sources used by VATSIM tools

- **Navigraph Navigation Data API (and SimBrief API).** Third-party apps may integrate it under the [Developer Terms](https://developers.navigraph.com/docs/developer-terms-of-service) (v1.0, 16 Dec 2021). Each end user signs in with their own subscription (OpenID Connect); the developer applies for a client ID and passes a review ([Navigation Data intro](https://developers.navigraph.com/docs/navigation-data/introduction)). Data is Jeppesen, worldwide, every 28-day AIRAC, delivered as Navigraph's DFD SQLite format. Constraints quoted from the terms: access "only in the format the data is provided ... without making any copies thereof (other than caching copies)" (§3.1b); use only for "Game-based Learning in Personal Computer Flight Simulator Software"; "You may not mix or combine any Navigraph Data with navigational or chart data received from any other data source, e.g. ... governmental authorities" for the same object type in the same region (§5.6, exceptions not read in full). "Default Navigraph Data" is free to end users but "will never be newer than twelve (12) months" (§8.1). The [restrictions page](https://developers.navigraph.com/docs/general/restrictions) adds that it may not "power any other type of system och application for any other purpose than flight simulation". Whether a VATSIM traffic-analysis TUI counts as flight simulation use is a question for Navigraph's review (**unverified**).
- **Little Navmap.** Ships "a navigation database courtesy of Navigraph" (`little_navmap_navigraph.sqlite`, an old bundled cycle, 1801 per the README), updated only with the user's own subscription via FMS Data Manager ([Little Navmap manual](https://www.littlenavmap.org/manuals/littlenavmap/release/latest/en/NAVDATA.html), [README](https://github.com/albar965/littlenavmap/), [update guide](https://albar965.github.io/littlenavmap_navigraph.html)). Otherwise it reads the user's simulator scenery. No open source of its own.
- **open flightmaps (OFM).** Run by the not-for-profit OFMA (Salzburg). The OFMA General Users' License grants "a worldwide, royalty-free, non-exclusive license ... These rights explicitly include commercial use", on two conditions: attribute OFM, and report errors back, with any app giving its users a way to report errors ([home](https://openflightmaps.org/), [about](https://openflightmaps.org/about/)); the licence text itself was not read (**unverified** wording). Coverage per the [downloads page](https://openflightmaps.org/downloads/): Belgium, Germany, Finland, Netherlands, Denmark, Poland, Sweden, South Africa, Namibia, Croatia, France, Greece, Hungary, Italy, Slovenia, Czech Republic, Malta, Austria (the list was cut off after LO; Switzerland, Romania, Bulgaria and Slovakia were listed in older news, **unverified** now). AIRAC-cycled, AIXM-based OFMX format ([OFMX docs](https://gitlab.com/openflightmaps/ofmx/wikis/home)). It is a VFR project: whether it holds IFR waypoints and airways is **unverified** (likely navaids, airspace, VFR points only).
- **Eurocontrol EAD.** EAD Basic is a free, registration-gated browser for AIPs, "for general purposes only"; content may be redistributed "for non commercial purposes" with acknowledgement but not otherwise ([EAD Basic](https://www.ead.eurocontrol.int/fwf-eadbasic/public/cms/cmscontent.faces?configKey=default.home.page)). Machine-readable AIXM downloads (SDO data download, AIMSL B2B) are an EAD Pro service: "Use of the services made available by the EAD is subject to the payment of charges ... service charges as well as royalty fees, applied by the Member States, when EAD data is made available by the client to third party end users" ([EAD service page](https://www.eurocontrol.int/service/european-ais-database)). Coverage ECAC+. Not usable at runtime by an open-source app without a paid contract.

## 3. Per-country machine-readable AIP data (sample)

Pattern: EU/UK rules (Reg 2017/373 AIS.OR.345) now require an AIP digital dataset in AIXM 5.1, so many European states publish one each AIRAC, but under the state's AIP copyright; outside Europe the data is usually sold. Eurocontrol keeps an index of state sources ([AIXM Data Sources](https://swim-eurocontrol.atlassian.net/wiki/spaces/AIX/pages/243799207)).

- **UK (NATS):** UK ICAO AIP Dataset, AIXM 5.1, every AIRAC, free download from [nats.aero/ais/datasets](https://nats-uk.ead-it.com/cms-nats/opencms/en/Publications/digital-datasets); "The data sets are for Aviation Use Only" ([circular 2026/W/003](https://nats-uk.ead-it.com/cms-nats/opencms/en/Publications/aip-supplements/EG_Circ_2026_W_003_en.pdf)); redistribution terms beyond that **unverified**.
- **Germany (DFS):** navaids, designated points, routes and procedures in AIXM 5.1.1 and Excel, NOW/NEXT/AFTER cycles plus a REST API ([aip.dfs.de/datasets](https://aip.dfs.de/datasets)); "may be used for aviation purposes only by the organisation to which they were issued ... no part ... may be ... redistributed, republished ... without the prior written permission" of DFS.
- **Canada (NAV CANADA):** paid annual subscription under a commercial data licence, data "protected under the Copyright Act of Canada" ([Data Sales](https://www.navcanada.ca/en/aeronautical-information/data-sales.aspx)).
- **Australia (Airservices):** IFR waypoints, routes and navaids as CSV in the "General Aviation Data" product, subscription required ([Data.Airservices](https://data.airservicesaustralia.com/data-product/general-aviation-data)); the older order form priced Product Group A at AUD 1,860 per quarter ([order form](https://www.airservicesaustralia.com/industry/dsorder/groups.asp)); current price and licence **unverified**.

## What this means for the app

Options, without a choice:

1. **Navigraph Navigation Data API, user's own subscription (opt-in).** Worldwide, AIRAC-current, includes procedures; legal through the developer route. Needs a client ID and Navigraph review, OIDC login in a TUI, a DFD SQLite reader, and a decision on whether the app's purpose fits "flight simulation". The §5.6 no-mixing rule may forbid merging it with NASR/CIFP in the US, so the app might have to use one source or the other per region. Users without a subscription get nothing (or 12-month-old default data, if Navigraph grants it).
2. **Ask AeroNav (and Navigraph) for approval as a VATSIM-connected tool** to read GNG sector-file output. Matches what controllers see, AIRAC-current, but covers about 110 FIRs only, needs a `.sct`/`.ese` parser, and depends on written permission not yet asked for.
3. **Per-country open datasets.** UK and Germany publish AIRAC AIXM 5.1 for free, but "aviation use only" and no-redistribution terms make runtime fetch by a third-party app doubtful without permission; each country is its own parser input and licence check.
4. **open flightmaps.** Openly licensed (attribution plus error-reporting duty) for about 18 countries, mostly Europe; probably lacks IFR airways, so it would add navaids/points only.
5. **Status quo plus partial layers** (OurAirports navaids, vatSpy airports): no airways, so a "partial resolution" rule as in the earlier note.

Design questions each raises:

- Option 1: Is opt-in login acceptable in a TUI, and how are expired subscriptions handled (Navigraph's review requires a safeguard)? Does the app drop NASR where Navigraph is loaded, to satisfy the no-mixing rule?
- Option 2: Who asks AeroNav and Navigraph, and what if only some vACCs' owners consent? How are FIRs without a package handled?
- Option 3: Which countries are worth the per-state AIXM work, and is written permission sought per state?
- Options 4 and 5: Is a route with unresolved airway segments still used for distance, or does it fall back to great circle (same question as the earlier note)?

## References

- [VATSIM x Navigraph partnership](https://forum.vatsim.net/t/partnership-announcement-vatsim-x-navigraph/10185)
- [VATSIM UK NavData Update Guide](https://docs.vatsim.uk/General/Use%20of%20Software/NavData%20Update%20Guide/)
- [AeroNav GNG about](https://gng.aero-nav.com/AERONAV/about), [GNG agreement](https://f.dev.aero-nav.com/AERONAV/agreement), [AeroNav AIRAC downloads](https://files.aero-nav.com/)
- [vatSys Data Preparation Kit](https://virtualairtrafficsystem.com/docs/dpk/), [vatSys/australia-dataset](https://github.com/vatSys/australia-dataset)
- [vatspy-data-project](https://github.com/vatsimnetwork/vatspy-data-project)
- [Navigraph Developer Terms](https://developers.navigraph.com/docs/developer-terms-of-service), [Navigation Data intro](https://developers.navigraph.com/docs/navigation-data/introduction), [Restrictions](https://developers.navigraph.com/docs/general/restrictions)
- [Little Navmap navdata manual](https://www.littlenavmap.org/manuals/littlenavmap/release/latest/en/NAVDATA.html)
- [open flightmaps](https://openflightmaps.org/), [about/licence summary](https://openflightmaps.org/about/), [downloads](https://openflightmaps.org/downloads/)
- [Eurocontrol EAD](https://www.eurocontrol.int/service/european-ais-database), [EAD Basic](https://www.ead.eurocontrol.int/fwf-eadbasic/public/cms/cmscontent.faces?configKey=default.home.page)
- [NATS digital datasets](https://nats-uk.ead-it.com/cms-nats/opencms/en/Publications/digital-datasets), [DFS data sets](https://aip.dfs.de/datasets), [NAV CANADA data sales](https://www.navcanada.ca/en/aeronautical-information/data-sales.aspx), [Airservices data](https://data.airservicesaustralia.com/data-product/general-aviation-data)
- [Eurocontrol AIXM Data Sources](https://swim-eurocontrol.atlassian.net/wiki/spaces/AIX/pages/243799207)
