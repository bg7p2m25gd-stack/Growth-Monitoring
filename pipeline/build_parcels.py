"""
Land, density and municipal tax base by neighbourhood, from the City of Edmonton property assessment roll.

Reads data/growth.json (made by build_data.py) for neighbourhood areas, dwellings and permits, and writes
data/parcels.json for the Density and Tax base tabs. Run it after build_data.py:

    .venv/bin/python pipeline/build_parcels.py

Method (see README "Density and tax base"):
- Current roll: Property Assessment (current) joined to Property Information (current) on account number,
  giving class, value, lot size, year built, building floor area and legal description for every account.
- Lot sizes are in square metres. Condo units carry their share of the parcel, so lot sizes add up without
  double counting. Tax-exempt land (roads, parks, schools) is not on the roll.
- An account with no year built has no finished building on it: a vacant lot or a building under construction.
- Unsubdivided legacy parcels (no registered plan in the legal description and larger than 2 ha) are kept
  apart, because one title can hold a few old buildings on a large area of undeveloped land.
- Accounts without a neighbourhood ID (e.g. "CHAPPELLE AREA") are placed by their coordinates.
- Homes in finished buildings = estimated dwellings now (growth.json) minus units on permits from the last
  three years that have no occupancy date yet. Occupancy dates are recorded for most permits from 2021 on;
  permits older than three years are assumed finished.
- Built density = homes in finished buildings / residential land that has a finished building on it.
- Municipal tax = assessed value x the City's municipal rate for the class (education tax excluded).
If the City can't be reached, the previous data/parcels.json is kept and the script exits non-zero.
"""
import datetime as dt
import json
import os
import statistics
import sys
import time
from collections import defaultdict

import requests
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "parcels.json")
BASE = "https://data.edmonton.ca/resource/"
DS = {"current": "q7d6-ambg", "info": "dkk9-cj3x", "historical": "qi6a-xuwt", "tax": "pwis-wc4c"}
SOURCES = {k: f"https://data.edmonton.ca/d/{v}" for k, v in DS.items()}
RATE_CLASS = {"RESIDENTIAL": "Residential", "OTHER RESIDENTIAL": "Other Residential", "COMMERCIAL": "Non Residential",
              "MA DERELICT RESIDENTIAL": "Mature Area Derelict Residential", "DERELICT RESIDENTIAL": "Mature Area Derelict Residential",
              "DESIGNATED IND PROPERTIES": "Non Residential", "NONRES MUNICIPAL/RES EDUCATION": "Non Residential", "FARMLAND": "Farmland"}
RES_CLASSES = ("RESIDENTIAL", "MA DERELICT RESIDENTIAL", "DERELICT RESIDENTIAL")


def bucket(cls):
    return "res" if cls in RES_CLASSES else "rental" if cls == "OTHER RESIDENTIAL" else "farm" if cls == "FARMLAND" else "nonres"
LEGACY_M2 = 20000
CONDO_MIN_VALUE = 75000          # below this a "Unit" account is a parking stall or storage locker
SAME_STAGE_YEARS = (5, 10, 15)
# Campus neighbourhoods: student residences are on the roll as rental buildings, but the census counts their
# rooms as group living quarters, not homes, and university residences are likely tax-exempt. Density and tax
# figures there would be meaningless, so they are left out and marked.
CAMPUS = {"UNIVERSITY OF ALBERTA"}
UNRELIABLE_ROLLS = {"2024"}      # residential land in the 2024 historical roll drops and recovers in many neighbourhoods
NAME_ALIASES = {"OLIVER": "WÎHKWÊNTÔWIN", "RAPPERSWIL": "RAPPERSWILL", "GLENRIDDING AREA": "GLENRIDDING HEIGHTS",
                "TERWILLEGAR SOUTH": "SOUTH TERWILLEGAR", "RIVER VALLEY WINDEMERE": "RIVER VALLEY WINDERMERE"}


def get(ds, params):
    for attempt in range(4):
        try:
            r = requests.get(BASE + DS[ds] + ".json", params=params, timeout=300)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))


def get_all(ds, params, page=50000):
    rows, offset = [], 0
    while True:
        chunk = get(ds, dict(params, **{"$limit": page, "$offset": offset, "$order": "account_number"}))
        rows += chunk
        if len(chunk) < page:
            return rows
        offset += page


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def municipal_rates():
    rows = get("tax", {"$where": "tax_rate_type = 'Municipal'", "$limit": 500})
    by_year = defaultdict(dict)
    for r in rows:
        by_year[r["tax_year"]][r["assessment_class"]] = f(r["amount_per_1_000_of_assessed_value"]) / 1000
    return by_year


def main():
    growth = json.load(open(os.path.join(DATA, "growth.json")))
    nbs = {n["id"]: n for n in growth["neighbourhoods"]}
    key_to_id = {n["key"]: i for i, n in nbs.items()}

    def nid_from_name(name):
        k = (name or "").strip().upper()
        return key_to_id.get(NAME_ALIASES.get(k, k))

    roll_year = str(growth.get("current_year") or dt.date.today().year)
    try:
        print("Tax rates")
        rates_all = municipal_rates()
        rate_year = roll_year if roll_year in rates_all else max(rates_all)
        rates = rates_all[rate_year]
        print("Current roll")
        cur = get_all("current", {"$select": "account_number, neighbourhood_id, mill_class_1, mill_class_2, mill_class_3, tax_class_pct_1, tax_class_pct_2, tax_class_pct_3, assessed_value, latitude, longitude"})
        print(f"  {len(cur)} accounts\nProperty information")
        info = get_all("info", {"$select": "account_number, lot_size, total_gross_area, year_built, legal_description"})
        print(f"  {len(info)} accounts\nHistorical land and values")
        hist_land = get("historical", {
            "$select": "neighbourhood_name, assessment_year, mill_class_1, sum(lot_size) as lot, count(*) as n",
            "$where": "mill_class_1 in('RESIDENTIAL','OTHER RESIDENTIAL')",
            "$group": "neighbourhood_name, assessment_year, mill_class_1", "$limit": 50000})
        hist_house = get("historical", {
            "$select": "neighbourhood_name, assessment_year, median(assessed_value) as m, count(*) as n",
            "$where": "mill_class_1 = 'RESIDENTIAL' AND legal_description like '%Lot:%' AND year_built is not null",
            "$group": "neighbourhood_name, assessment_year", "$limit": 50000})
        hist_condo = get("historical", {
            "$select": "neighbourhood_name, assessment_year, median(assessed_value) as m, count(*) as n",
            "$where": f"mill_class_1 = 'RESIDENTIAL' AND legal_description like '%Unit:%' AND assessed_value >= {CONDO_MIN_VALUE}",
            "$group": "neighbourhood_name, assessment_year", "$limit": 50000})
    except Exception as e:
        print(f"Assessment fetch failed ({e}); keeping the existing {OUT}", file=sys.stderr)
        sys.exit(1)

    info_by = {r["account_number"]: r for r in info}
    gj = json.load(open(os.path.join(DATA, "neighbourhoods.geojson")))
    polys = [shape(ft["geometry"]) for ft in gj["features"]]
    poly_ids = [ft["properties"]["id"] for ft in gj["features"]]
    tree = STRtree(polys)

    def locate(r):
        try:
            pt = Point(float(r["longitude"]), float(r["latitude"]))
        except (KeyError, TypeError, ValueError):
            return None
        for i in tree.query(pt):
            if polys[i].contains(pt):
                return poly_ids[i]
        return None
    located = 0
    zero = lambda: defaultdict(float)
    land, tax, floor, vac_tax = defaultdict(zero), defaultdict(zero), defaultdict(zero), defaultdict(float)
    houses, condos = defaultdict(list), defaultdict(list)
    nonres_v = defaultdict(float)
    unmatched = 0
    for r in cur:
        nid = r.get("neighbourhood_id")
        if nid not in nbs:
            nid = locate(r)
            if nid not in nbs:
                continue
            located += 1
        p = info_by.get(r["account_number"])
        if not p:
            unmatched += 1
            continue
        cls, v, lot = r.get("mill_class_1") or "", f(r.get("assessed_value")), f(p.get("lot_size"))
        legal = p.get("legal_description") or ""
        built = bool(p.get("year_built"))
        # mixed-class accounts: split the value by each class's percentage before applying its rate
        parts = [(r.get(f"mill_class_{k}"), f(r.get(f"tax_class_pct_{k}"))) for k in (1, 2, 3) if r.get(f"mill_class_{k}")]
        if not parts or sum(pc for _, pc in parts) <= 0:
            parts = [(cls, 100.0)]
        share = sum(pc for _, pc in parts)
        tparts = defaultdict(float)
        for c, pc in parts:
            tparts[bucket(c)] += v * pc / share * rates.get(RATE_CLASS.get(c, ""), 0)
        t = sum(tparts.values())
        if "Plan:" not in legal and lot > LEGACY_M2:
            land[nid]["legacy"] += lot
            vac_tax[nid] += t
            continue
        if cls in RES_CLASSES:
            cat = "res_built" if built else "res_unbuilt"
        elif cls == "OTHER RESIDENTIAL":
            cat = "rental_built" if built else "rental_unbuilt"
        elif cls == "FARMLAND":
            cat = "farmland"
        else:
            cat = "nonres_built" if built else "nonres_unbuilt"
        land[nid][cat] += lot
        if cat == "nonres_built":
            nonres_v[nid] += v
        if built and cat != "farmland":
            for b, tv in tparts.items():
                if b != "farm":
                    tax[nid][b] += tv
            floor[nid][cat] += f(p.get("total_gross_area"))
        else:
            vac_tax[nid] += t
        if cls == "RESIDENTIAL" and built and "Lot:" in legal:
            houses[nid].append(v)
        elif cls == "RESIDENTIAL" and "Unit:" in legal and v >= CONDO_MIN_VALUE:
            condos[nid].append(v)

    land_hist, values = defaultdict(dict), defaultdict(dict)
    for r in hist_land:
        nid = nid_from_name(r.get("neighbourhood_name"))
        if nid:
            y = r["assessment_year"]
            land_hist[nid][y] = land_hist[nid].get(y, 0) + f(r.get("lot")) / 1e4
    for kind, rows in (("house", hist_house), ("condo", hist_condo)):
        for r in rows:
            nid = nid_from_name(r.get("neighbourhood_name"))
            if nid:
                values[nid].setdefault(r["assessment_year"], {})[kind] = [round(f(r.get("m"))), int(f(r.get("n")))]
    for nid in nbs:
        for kind, vals in (("house", houses[nid]), ("condo", condos[nid])):
            if vals:
                values[nid].setdefault(roll_year, {})[kind] = [round(statistics.median(vals)), len(vals)]

    out = {}
    for nid, n in nbs.items():
        L = {k: round(v / 1e4, 2) for k, v in land[nid].items()}
        if not L:
            continue
        assessed = sum(L.values())
        L["unassessed"] = round(max(n["area_ha"] - assessed, 0), 2)
        # land on the roll can't exceed the neighbourhood; where it does, the source lot sizes are wrong
        land_error = assessed > n["area_ha"] * 1.05
        homes = None
        if n.get("dwell_est_now") is not None:
            pending = sum(max((n["permits"].get(str(y)) or {}).get("units", 0) - (n["permits"].get(str(y)) or {}).get("occ", 0), 0)
                          for y in range(int(roll_year) - 2, int(roll_year) + 1))
            homes = max(round(n["dwell_est_now"] - pending), 0)
        res_land = L.get("res_built", 0) + L.get("rental_built", 0) + L.get("res_unbuilt", 0) + L.get("rental_unbuilt", 0)
        built_res = L.get("res_built", 0) + L.get("rental_built", 0)
        built_all = built_res + L.get("nonres_built", 0)
        T = {k: round(v) for k, v in tax[nid].items()}
        res_tax = T.get("res", 0) + T.get("rental", 0)
        fl = floor[nid]
        rec = {
            "land": L,
            "homes": homes,
            "built_density": round(homes / built_res, 1) if homes and built_res >= 1 else None,
            "net_density": round(homes / res_land, 1) if homes and res_land >= 1 else None,
            "gross_density": round(homes / n["area_ha"], 1) if homes is not None and n["area_ha"] else None,
            "far": round((fl["res_built"] + fl["rental_built"]) / (built_res * 1e4), 2) if built_res >= 1 else None,
            "tax": T,
            "tax_unbuilt": round(vac_tax[nid]),
            "tax_per_ha": round(sum(T.values()) / built_all) if built_all >= 1 else None,
            "res_tax_per_ha": round(res_tax / built_res) if built_res >= 1 else None,
            "nonres_value_per_ha": round(nonres_v[nid] / L["nonres_built"]) if L.get("nonres_built", 0) >= 1 else None,
            "tax_per_home": round(res_tax / homes) if homes and homes >= 20 else None,
            "values": dict(sorted(values[nid].items())),
            "res_land_hist": {y: round(v, 2) for y, v in sorted(land_hist[nid].items()) if y not in UNRELIABLE_ROLLS},
        }
        if land_error:
            for k in ("built_density", "net_density", "gross_density", "far", "res_tax_per_ha", "tax_per_home", "tax_per_ha", "nonres_value_per_ha"):
                rec[k] = None
            rec["note"] = "land_error"
        if n["key"] in CAMPUS:
            for k in ("built_density", "net_density", "gross_density", "far", "res_tax_per_ha", "tax_per_home", "tax_per_ha"):
                rec[k] = None
            rec["note"] = "campus"
        # same-stage density: homes permitted by year X / residential land on the roll of year X+1
        fp = (n.get("first_permit") or {}).get("date")
        pre = (n.get("dwell") or {}).get("2016") or 0
        if fp and int(fp[:4]) >= 2010 and pre < 100:
            cum, by_year = 0, {}
            for y in range(2009, int(roll_year) + 1):
                p = n["permits"].get(str(y)) or {}
                cum += p.get("units", 0) - p.get("demo", 0)
                by_year[y] = cum
            stage = {}
            for k in SAME_STAGE_YEARS:
                y = int(fp[:4]) + k
                ha = rec["res_land_hist"].get(str(y + 1)) if str(y + 1) != roll_year else res_land
                if y + 1 <= int(roll_year) and ha and ha >= 1:
                    stage[str(k)] = round(by_year[y] / ha, 1)
            rec["same_stage"] = stage
        out[nid] = rec

    result = {
        "generated": dt.datetime.now().isoformat(timespec="minutes"),
        "roll_year": roll_year, "condition_date": f"{int(roll_year) - 1}-12-31",
        "tax_rate_year": rate_year, "municipal_rates": {k: round(v * 1000, 4) for k, v in rates.items()},
        "sources": SOURCES, "legacy_min_ha": LEGACY_M2 / 1e4, "condo_min_value": CONDO_MIN_VALUE,
        "unreliable_rolls": sorted(UNRELIABLE_ROLLS), "unmatched_accounts": unmatched, "located_by_coordinates": located,
        "neighbourhoods": out,
    }
    with open(OUT, "w") as fh:
        json.dump(result, fh, separators=(",", ":"), ensure_ascii=False)
    print(f"Wrote {OUT}: {len(out)} neighbourhoods, {unmatched} accounts without property information, "
          f"{os.path.getsize(OUT) // 1024} KB")


if __name__ == "__main__":
    main()
