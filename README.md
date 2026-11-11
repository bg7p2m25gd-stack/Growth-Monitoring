# Edmonton Growth Monitor

Neighbourhood growth, density and assessment trends for Edmonton, cross-referenced with the
buildout targets in approved statutory plans (NSPs / NASPs).

## Files
- `index.html` — the dashboard (map, plan buildout ledger, compare, full table). Reads `data/`.
- `pipeline/build_data.py` — pulls City of Edmonton open data and writes `data/*.json`.
- `pipeline/plan_targets.json` — planned units, population, density (du/nrha) and low-density share,
  transcribed by hand from each plan's Land Use & Population Statistics table. **Update this when a
  plan amendment changes its statistics** (the bylaw number is recorded for each plan).
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
