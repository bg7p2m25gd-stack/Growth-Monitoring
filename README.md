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

## Data sources (data.edmonton.ca)
General Building Permits (24uj-dj8v), Development Permits (2ccn-pwtu), Property Assessment current
(q7d6-ambg) and historical (qi6a-xuwt), Neighbourhoods (65fr-66s6), Plans in Effect (jdmr-bidz),
2016/2021 federal and 2019 municipal census by neighbourhood.

## Known limits
- Permits are approvals, not completions. "Dwellings today" = 2021 census occupied dwellings + net
  units permitted since, so it runs slightly ahead of what is built.
- ARPs are measured against the target each plan states (total units, new units since adoption, or residents), recorded in `plan_targets.json` with `kind: "arp"`. North Edge and the River Valley ARP state no numeric target.
- Area structure plans (ASPs) and industrial plans have no transcribed targets yet.
- Average assessment mixes houses and condo units, so it reflects housing mix as well as prices.
