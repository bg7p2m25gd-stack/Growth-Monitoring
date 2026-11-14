"""
CMHC housing starts, completions and units under construction for the City of Edmonton and the Edmonton
region (CMA), by dwelling type and intended market, monthly from January 2009.

Source: CMHC Starts and Completions Survey, via the Housing Market Information Portal
(https://www03.cmhc-schl.gc.ca/hmip-pimh/). Used under the CMHC Licence Agreement for the Use of Data.
Writes data/cmhc.json. If CMHC can't be reached, the previous file is kept and the script exits non-zero.

    .venv/bin/python pipeline/build_cmhc.py
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "cmhc.json")
URL = "https://www03.cmhc-schl.gc.ca/hmip-pimh/en/TableMapChart/RenderTable"
START_YEAR = 2009

GEOS = {"city": ("4811061", "4", "City of Edmonton"), "cma": ("0340", "3", "Edmonton region (CMA)")}
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


def main():
    result = {"generated": dt.datetime.now().isoformat(timespec="minutes"),
              "source": "CMHC Starts and Completions Survey (Housing Market Information Portal)",
              "geos": {k: v[2] for k, v in GEOS.items()}, "markets": MARKETS, "data": {}}
    months = set()
    try:
        for g, (gid, gtype, _) in GEOS.items():
            for s, (tid, measure) in SERIES.items():
                for mk in MARKETS:
                    tab = fetch(tid, measure, gid, gtype, mk)
                    months |= set(tab)
                    result["data"].setdefault(g, {}).setdefault(s, {})[mk] = tab
                    time.sleep(0.5)
                    print(f"  {g} {s} {mk}: {len(tab)} months")
    except Exception as e:
        print(f"CMHC fetch failed ({e}); keeping the existing {OUT}", file=sys.stderr)
        sys.exit(1)
    months = sorted(months)
    # last month with published starts for the city (the portal lists future months of the current year as blank)
    city_all = result["data"]["city"]["starts"]["All"]
    last = max(m for m in months if city_all.get(m, {}).get("all") is not None)
    months = [m for m in months if m <= last]
    # compact layout: data[geo][series][market][type] = [values aligned to months]
    compact = {g: {s: {mk: {t: [tab.get(m, {}).get(t) for m in months] for t in TYPES.values()}
                       for mk, tab in by_mk.items()} for s, by_mk in by_s.items()} for g, by_s in result["data"].items()}
    result.update(months=months, last_month=last, data=compact)
    with open(OUT, "w") as fh:
        json.dump(result, fh, separators=(",", ":"))
    print(f"Wrote {OUT}: {months[0]} to {last}")


if __name__ == "__main__":
    main()
