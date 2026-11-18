# Edmonton Growth Monitor

Neighbourhood growth, density and assessment trends for Edmonton, cross-referenced with the
buildout targets in approved statutory plans (NSPs / NASPs).

## Files
- `index.html` — the dashboard (map, plan buildout ledger, compare, full table). Reads `data/`.
- `pipeline/build_data.py` — pulls City of Edmonton open data and writes `data/*.json`.
- `pipeline/plan_targets.json` — planned units, population, density (du/nrha) and low-density share,
  transcribed by hand from each plan's Land Use & Population Statistics table. **Update this when a
  plan amendment changes its statistics** (the bylaw number is recorded for each plan).
- `pipeline/plan_dates.json` — each plan's adopting bylaw and approval date, read from the bylaw history
  in its consolidation by `pipeline/extract_plan_dates.py`. Entries marked `verified` were checked by hand.
- `pipeline/first_permit_overrides.json` — hand-checked first-permit dates where the automatic rule is
  fooled by houses on old acreages (currently Horse Hill ASP and Marquis NSP).
- `pipeline/build_cmhc.py` — fetches CMHC housing starts, completions and units under construction (City of
  Edmonton, the Edmonton CMA and 13 other municipalities, monthly from 2009, by dwelling type and intended market) into `data/cmhc.json`.
- `pipeline/build_region.py` — writes `data/region.geojson`, the municipal boundaries (Statistics Canada 2021
  census subdivisions) for the regional map on the CMHC tab. Run by hand after a new census; not part of the weekly refresh.
- `pipeline/build_parcels.py` — joins the current assessment roll to Property Information by account number
  and writes `data/parcels.json` for the Density and Tax base tabs. Runs after `build_data.py` (it reads
  `data/growth.json`).
- `pipeline/extract_plan_text.py`, `pipeline/render_pages.py` — helpers for reading plan PDFs.
- `refresh.sh` — re-pulls all data.

## Refresh
The data refreshes automatically every Monday at 6:00 (Edmonton time) via GitHub Actions
(`.github/workflows/refresh-data.yml`). To refresh on demand: GitHub repo → **Actions** →
**Refresh data** → **Run workflow**. The live site updates a minute or two after the run finishes.

To refresh locally instead:
```
./refresh.sh
```
Then view locally (`python3 -m http.server` in this folder, open http://localhost:8000) or republish
the artifact.

## Unit counting rules
Units come from `units_added` on each building permit, split by housing type (`split_units` in
`pipeline/build_data.py`):

- **Secondary suites** are moved out of house permits: a single-detached permit's units above 1 and a
  semi-detached permit's units above 2 are suites; row-house suites come from the stated count
  ("3 Secondary Suites") or the units above the stated dwellings ("6 unit Row House … suites in unit 16
  and 21"). "Add Suites to Single Dwelling" permits, and suites added to an existing house, are suites.
- **Garden / garage suites**: Backyard House permits, and house or semi permits whose description is a
  garage or garden suite in an accessory building.
- **New units in existing apartment buildings** ("Add Suites to Multi-Dwelling" on an apartment or
  mixed-use building) count as apartments, not suites. Office-to-residential conversions count.
- **Home-improvement permits published without a unit count.** Since mid-2018 the City's open data
  lists "Home Improvement" permits with no work type and no `units_added`, only a description. That includes
  nearly every secondary suite added to an existing house and most house demolitions, so
  `home_improvement_units` reads the City's standard wording:
  - a suite or garden suite only when the description starts "To add / develop / construct / create a
    Dwelling (Secondary Suite, Garden Suite …)"; 1 unit, or the number stated (up to 4). Suites that existed
    without permits and are made legal are included, as the City counts them;
  - a demolition only when, after setting aside garages, sheds and accessory buildings, a home is named:
    a house or mobile home is 1 unit, a semi-detached house or duplex 2; row-house and apartment
    demolitions with no stated size are left out.
  Checked against the City's Residential Building Permit Activities dashboard (gis.edmonton.ca, layer
  `Hosted/BUILDING_PERMITS_Dashboard`, updated quarterly), 2017–2025: suites within 2–9% (slightly under)
  every year and demolitions within about 6%; by neighbourhood, 2024–25, 210 of 222 within 10% for suites
  and 169 of 173 for demolitions. Fewer than 1% of the added suites are at an address that already had a
  suite counted on another permit.
- **Excluded** (shown in the site's Sources and method panel with counts):
  - foundation / structural-frame permits whose building also has a "New" permit (same units twice);
  - a second new-house permit at the same address (re-issue of a lapsed permit), unless a demolition
    happened in between or it is a garage suite;
  - alteration permits with a unit count but no new homes described (fire repairs, solar panels, riser
    replacements, condo fit-outs of units already counted, legalizing existing units, unit merges);
  - an extra unit on a house whose description says the basement is "not to be used as a secondary
    suite / additional dwelling".

Checked against the Blatchford pace workbook (11 neighbourhoods, within about 1% per type) and the 2021
census for 27 greenfield neighbourhoods: singles, semis and apartments match within 0–15% once a year of
construction lag is allowed; row houses run about 30% above the census (classification and occupancy,
not suites); suites cannot be checked against the census because many are not finished or occupied.

## CMHC housing starts vs building permits
The "Housing starts (CMHC)" tab uses CMHC's Starts and Completions Survey, retrieved weekly from the Housing
Market Information Portal (used under the CMHC Licence Agreement for the Use of Data). A **start** is counted
when construction begins (usually when the whole footing is poured), by CMHC site visits; a **building permit**
is the City's approval to build, counted when issued. Starts exclude renovations and conversions within existing
buildings (so suites added to existing homes and office conversions are not starts), classify stacked
townhouses, duplexes and triplexes as "apartment and other", and record the intended market (homeowner,
rental, condo, co-op). "Rental" = purpose-built rental. CMHC has not published Edmonton completions or units
under construction for January 2023 to June 2025; those months are shown as gaps.
The data now covers 14 municipalities individually (the City of Edmonton plus 13 others) and an "other" group, which is
the region total minus those 14. If a fetch for a smaller municipality fails, its previous figures are carried forward
and the tab says which areas were not refreshed.

## Density and tax base
- **Land** comes from lot sizes on the assessment roll (square metres). Condo units each carry their share of
  the parcel, so lot sizes normally add up without double counting. Tax-exempt land such as roads, parks and
  schools is not on the roll. Land is assigned by an account's first class; derelict residential counts as
  residential. Where a neighbourhood's land on the roll exceeds its area by more than 5%, the source lot sizes
  are wrong and its density and tax figures are left out (`note: "land_error"`; currently Pembina, where four
  condo plans carry lot sizes that rise by 2 m² per unit).
- An account with **no year built** has no finished building (an empty lot or one under construction).
- **Unsubdivided parcels** (no registered plan in the legal description, including a blank one, and larger
  than 2 ha, in any class) are kept
  separate: one old title can hold a few buildings on a large area of undeveloped land (e.g. Griesbach).
- Accounts with no neighbourhood ID on the roll (e.g. "CHAPPELLE AREA") are placed by their coordinates.
- **Homes** for density are homes in finished buildings: estimated dwellings now minus units on permits from
  the current year and the two before it that have no occupancy date yet (occupancy dates are recorded for most permits from
  2021). Older permits are assumed finished.
- **Built density** = those homes ÷ residential land (homes and rental buildings) with a finished building.
  **Gross density** = homes ÷ the whole neighbourhood.
- **Municipal tax** = assessed value × the City's municipal rate for the class (education tax excluded).
  Mixed-class accounts are split by their class percentages first. Tax is counted only for accounts with a
  finished building, so it matches the land and homes it is divided by; tax on vacant, under-construction and
  unsubdivided land is kept separately (`tax_unbuilt`) and not shown.
  **Residential tax per hectare** = residential + rental tax ÷ hectares of finished residential land;
  **tax per home** = the same tax ÷ homes in finished buildings. **Floor area ratio** = private floor area of
  finished residential buildings ÷ their land. Measures are left blank where the land they divide by is under
  1 ha or there are fewer than 20 homes.
  Only residential and "other residential" (rental buildings of 4+ units) tax is shown. The open roll
  includes tax-exempt institutions (e.g. NAIT, 11762 106 St, assessed as commercial) without flagging them,
  so non-residential property is shown as assessed value per hectare, not tax.
- **Typical values** are medians: houses = residential accounts on their own lot ("Lot:" in the legal
  description) with a building; condos = residential-class "Unit:" accounts worth at least $75,000, which leaves out parking
  stalls and storage units.
- The **University of Alberta** neighbourhood is left out of density and tax figures: its student residences
  are on the roll as rental buildings, but the census counts residence rooms as group living quarters, not
  homes, and university residences are generally tax-exempt (`CAMPUS` in `build_parcels.py`).
- The 2024 historical roll shows residential land dropping and recovering in many neighbourhoods, so it is
  left out of land trends.

## Data sources (data.edmonton.ca)
General Building Permits (24uj-dj8v), Development Permits (2ccn-pwtu), Property Assessment current
(q7d6-ambg) and historical (qi6a-xuwt), Property Information (dkk9-cj3x), Property and Education Tax
Rates (pwis-wc4c), Neighbourhoods (65fr-66s6), Plans in Effect (jdmr-bidz),
2016/2021 federal and 2019 municipal census by neighbourhood.

## Known limits
- Permits are approvals, not completions. "Dwellings today" = 2021 census occupied dwellings + net
  units permitted since, so it runs slightly ahead of what is built.
- ARPs are measured against the target each plan states (total units, new units since adoption, or residents), recorded in `plan_targets.json` with `kind: "arp"`. North Edge and the River Valley ARP state no numeric target.
- Area structure plans (ASPs) and industrial plans have no transcribed targets yet.
- Average assessment mixes houses and condo units, so it reflects housing mix as well as prices.
