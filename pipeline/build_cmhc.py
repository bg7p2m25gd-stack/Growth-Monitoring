"""
CMHC housing starts, completions and units under construction for the Edmonton region (CMA) and each of
its larger municipalities, by dwelling type and intended market, monthly from January 2009.

The 14 municipalities listed in GEOS are fetched one by one. Everything else in the region (small towns,
villages, summer villages and First Nations) is derived as "other" = region total minus the 14.

Source: CMHC Starts and Completions Survey, via the Housing Market Information Portal
(https://www03.cmhc-schl.gc.ca/hmip-pimh/). Used under the CMHC Licence Agreement for the Use of Data.
Writes data/cmhc.json. If CMHC can't be reached for the City or the region, the previous file is kept and
the script exits non-zero. If only a smaller municipality fails, its previous figures are carried forward.

    .venv/bin/python pipeline/build_cmhc.py
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "cmhc.json")
URL = "https://www03.cmhc-schl.gc.ca/hmip-pimh/en/TableMapChart/RenderTable"
START_YEAR = 2009

# key: (portal geography id, geography type, label). Type 4 ids are Statistics Canada municipality codes,
# each verified against the portal's own municipality breakdown of the region (table 1.1.1.9).
GEOS = {
    "city": ("4811061", "4", "City of Edmonton"),
    "cma": ("0340", "3", "Edmonton region (CMA)"),
    "st_albert": ("4811062", "4", "St. Albert"),
    "strathcona": ("4811052", "4", "Strathcona County"),
    "spruce_grove": ("4811049", "4", "Spruce Grove"),
    "leduc": ("4811016", "4", "Leduc"),
    "beaumont": ("4811013", "4", "Beaumont"),
    "fort_saskatchewan": ("4811056", "4", "Fort Saskatchewan"),
    "stony_plain": ("4811048", "4", "Stony Plain"),
    "leduc_county": ("4811012", "4", "Leduc County"),
    "parkland_county": ("4811034", "4", "Parkland County"),
    "sturgeon_county": ("4811059", "4", "Sturgeon County"),
    "morinville": ("4811068", "4", "Morinville"),
    "devon": ("4811018", "4", "Devon"),
    "calmar": ("4811019", "4", "Calmar"),
}
REQUIRED = ("city", "cma")
OTHER = ("other", "Other small municipalities")
# portal table id and default data field for each historical series
SERIES = {"starts": ("1.2.1", "measure-12"), "completions": ("1.2.2", "measure-11"), "under_construction": ("1.2.3", "measure-13")}
MARKETS = ["All", "Homeowner", "Rental", "Condo", "Co-Op"]
TYPES = {"Single": "single", "Semi-Detached": "semi", "Row": "row", "Apartment": "apartment", "All": "all"}
MONTH = {m: i + 1 for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def fetch(table_id, measure, geo_id, geo_type, market):
    today = dt.date.today()
    data = {
        "TableId": table_id, "GeographyId": geo_id, "GeographyTypeId": geo_type, "BreakdownGeographyTypeId": "0",
        "DisplayAs": "Table", "Ytd": "false", "DefaultDataField": measure, "Survey": "Scss", "DataSource": "1",
        "ForTimePeriod.Year": str(START_YEAR), "ForTimePeriod.Quarter": "1", "ForTimePeriod.Month": "1",
        "ToTimePeriod.Year": str(today.year), "ToTimePeriod.Quarter": "4", "ToTimePeriod.Month": "12",
        "AppliedFilters[0].Key": "dimension-18", "AppliedFilters[0].Value": market,
    }
    for attempt in range(4):
        try:
            r = requests.post(URL, data=data, timeout=120)
            r.raise_for_status()
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
        if cells:
            rows.append(cells)
    if not rows or rows[0][1:] != ["Single", "Semi-Detached", "Row", "Apartment", "All"]:
        raise ValueError(f"unexpected table layout for {table_id} {geo_id} {market}: {rows[:1]}")
    cols = [TYPES[c] for c in rows[0][1:]]
    out = {}
    for cells in rows[1:]:
        m = re.match(r"([A-Z][a-z]{2}) (\d{4})$", cells[0])
        if not m or len(cells) != 6:
            continue
        key = f"{m.group(2)}-{MONTH[m.group(1)]:02d}"
        out[key] = {c: (None if v in ("..", "", "x", "**") else int(v.replace(",", ""))) for c, v in zip(cols, cells[1:])}
    return out


def fetch_geo(gid, gtype):
    """All series and markets for one geography: {series: {market: {month: {type: value}}}}."""
    jobs = [(s, mk) for s in SERIES for mk in MARKETS]
    with ThreadPoolExecutor(3) as ex:
        tabs = list(ex.map(lambda j: fetch(SERIES[j[0]][0], SERIES[j[0]][1], gid, gtype, j[1]), jobs))
    out = {}
    for (s, mk), tab in zip(jobs, tabs):
        out.setdefault(s, {})[mk] = tab
    return out


def previous():
    """The existing file as {geo: {series: {market: {month: {type: value}}}}}, or {} if there is none."""
    try:
        old = json.load(open(OUT))
    except Exception:
        return {}
    ms = old["months"]
    return {g: {s: {mk: {m: {t: arr[i] for t, arr in by_t.items()} for i, m in enumerate(ms)}
                    for mk, by_t in by_mk.items()} for s, by_mk in by_s.items()} for g, by_s in old["data"].items()}


def main():
    raw, stale, old = {}, [], None
    for g, (gid, gtype, label) in GEOS.items():
        try:
            raw[g] = fetch_geo(gid, gtype)
            print(f"  {label}: {len(raw[g]['starts']['All'])} months")
        except Exception as e:
            if g in REQUIRED:
                print(f"CMHC fetch failed for {label} ({e}); keeping the existing {OUT}", file=sys.stderr)
                sys.exit(1)
            old = previous() if old is None else old
            if g not in old:
                print(f"CMHC fetch failed for {label} ({e}) and there is no earlier data; keeping the existing {OUT}", file=sys.stderr)
                sys.exit(1)
            raw[g] = old[g]
            stale.append(g)
            print(f"  {label}: fetch failed ({e}); carrying forward the previous figures", file=sys.stderr)
        time.sleep(0.5)
    # last month with published starts for the city (the portal lists future months of the current year as blank)
    city_all = raw["city"]["starts"]["All"]
    last = max(m for m, v in city_all.items() if v.get("all") is not None)
    months = sorted(m for m in set().union(*(raw[g]["starts"]["All"].keys() for g in REQUIRED)) if m <= last)
    # compact layout: data[geo][series][market][type] = [values aligned to months]
    compact = {g: {s: {mk: {t: [tab.get(m, {}).get(t) for m in months] for t in TYPES.values()}
                       for mk, tab in by_mk.items()} for s, by_mk in by_s.items()} for g, by_s in raw.items()}
    # everything else in the region = region total minus the listed municipalities (blank if any part is unpublished)
    members = [g for g in GEOS if g != "cma"]

    def rest(s, mk, t, i):
        total = compact["cma"][s][mk][t][i]
        parts = [compact[g][s][mk][t][i] for g in members]
        return None if total is None or any(p is None for p in parts) else total - sum(parts)

    compact[OTHER[0]] = {s: {mk: {t: [rest(s, mk, t, i) for i in range(len(months))] for t in TYPES.values()}
                             for mk in MARKETS} for s in SERIES}
    result = {"generated": dt.datetime.now().isoformat(timespec="minutes"),
              "source": "CMHC Starts and Completions Survey (Housing Market Information Portal)",
              "geos": {**{k: v[2] for k, v in GEOS.items()}, OTHER[0]: OTHER[1]},
              "municipalities": members + [OTHER[0]], "stale": stale,
              "markets": MARKETS, "months": months, "last_month": last, "data": compact}
    with open(OUT, "w") as fh:
        json.dump(result, fh, separators=(",", ":"))
    print(f"Wrote {OUT}: {months[0]} to {last}, {len(compact)} geographies, {os.path.getsize(OUT) // 1024} KB")


if __name__ == "__main__":
    main()
