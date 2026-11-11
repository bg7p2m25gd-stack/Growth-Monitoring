"""
Edmonton Neighbourhood Growth Monitor — data pipeline.

Pulls City of Edmonton open data (data.edmonton.ca), aggregates it to neighbourhoods
and statutory plan areas, and writes:
  data/growth.json              neighbourhood + plan metrics
  data/neighbourhoods.geojson   simplified boundaries
  data/plans.geojson            simplified statutory plan boundaries

Re-run any time to refresh:  .venv/bin/python pipeline/build_data.py
"""
import datetime as dt
import json
import os
import re
import time
from collections import defaultdict

import requests
from pyproj import Geod
from shapely.geometry import Point, mapping, shape
from shapely.strtree import STRtree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(ROOT, "cache", "api")
BASE = "https://data.edmonton.ca/resource/"
GEOD = Geod(ellps="WGS84")

DATASETS = {
    "neighbourhoods": "65fr-66s6",
    "building_permits": "24uj-dj8v",
    "development_permits": "2ccn-pwtu",
    "assessment_current": "q7d6-ambg",
    "assessment_historical": "qi6a-xuwt",
    "plans_in_effect": "jdmr-bidz",
    "census_2016_population": "tn7y-5nua",
    "census_2016_dwelling_types": "vidw-9hv8",
    "census_2019_population": "akuj-yrqt",
    "census_2019_dwelling_status": "522q-3877",
    "census_2021_population": "eg3i-f4bj",
    "census_2021_short_form": "rw8s-vqrs",
    "census_2016_boundaries": "3did-mjnj",
    "census_2021_boundaries": "5bk4-5txu",
}


# ---------------------------------------------------------------- fetching

def soql(dataset, params, page=50000, cache_key=None, max_age_h=20):
    """Run a SoQL query, paging through results. Cached on disk for max_age_h hours."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, (cache_key or dataset) + ".json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_h * 3600:
        with open(path) as fh:
            return json.load(fh)
    rows, offset = [], 0
    while True:
        q = dict(params, **{"$limit": page, "$offset": offset})
        for attempt in range(4):
            try:
                r = requests.get(BASE + DATASETS[dataset] + ".json", params=q, timeout=300)
                r.raise_for_status()
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))
        batch = r.json()
        rows.extend(batch)
        print(f"  {cache_key or dataset}: {len(rows)} rows")
        if len(batch) < page:
            break
        offset += page
    with open(path, "w") as fh:
        json.dump(rows, fh)
    return rows


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def area_ha(geom):
    return abs(GEOD.geometry_area_perimeter(geom)[0]) / 10000.0


def simplified(geom, tol=0.00015):
    g = geom.simplify(tol, preserve_topology=True)
    return json.loads(json.dumps(mapping(g), default=float),
                      parse_float=lambda s: round(float(s), 5))


# ---------------------------------------------------------------- classification

def housing_type(building_type, work_type):
    b = (building_type or "").lower()
    w = (work_type or "").lower()
    if "add suites" in w or "secondary suite" in b:
        return "suite"
    if "backyard" in b or "garden" in b:
        return "backyard"
    if "single detached" in b or "mobile home" in b:
        return "single"
    if "semi-detached" in b or "duplex" in b:
        return "semi"
    if "row house" in b:
        return "row"
    if "apartment" in b or "mixed use" in b:
        return "apartment"
    return "other"


WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12}
SUITE_N = re.compile(r"\b(\d+|" + "|".join(WORD_NUM) + r")\s*\(?\d*\)?\s*-?\s*(?:secondary|secondry)\s+suites?", re.I)
SUITE_ANY = re.compile(r"(?:secondary|secondry)\s+suite", re.I)
NO_SUITE = re.compile(r"\b(?:no|not\s+to\s+be(?:\s+used\s+as)?(?:\s+an?)?)\s+(?:secondary|secondry)\s+suite|not\s+to\s+be\s+used\s+as\s+an\s+additional\s+dwelling|no\s+secondary\s+suite\s+involved", re.I)
GARDEN = re.compile(r"garden\s+suite|garage\s+suite|backyard\s+house|laneway\s+house", re.I)
DWELLINGS = re.compile(r"\b(\d+|" + "|".join(WORD_NUM) + r")\s*[- ]\s*(?:dwelling|unit)s?\b", re.I)


def _n(k):
    k = k.lower()
    return int(k) if k.isdigit() else WORD_NUM[k]


def split_units(r):
    """Split a permit's added units by housing type, moving secondary and garden suites out of the
    house types using the permit description (rules from the Blatchford pace workbook, Notes tab)."""
    u = num(r.get("units_added"))
    ht = housing_type(r.get("building_type"), r.get("work_type"))
    desc = r.get("job_description") or ""
    garden = bool(GARDEN.search(desc))
    suite_said = bool(SUITE_ANY.search(desc)) and not NO_SUITE.search(desc)
    if ht == "suite":
        return {"backyard" if garden and not suite_said else "suite": u}
    if ht == "single":
        if garden and re.search(r"accessory", desc, re.I):
            return {"backyard": u}
        extra = max(u - 1, 0)
        return {"single": u - extra, **({"backyard" if garden and not suite_said else "suite": extra} if extra else {})}
    if ht in ("semi", "row"):
        if garden and not re.search(r"semi-detached house|row ?hous|dwelling", desc, re.I):
            return {"backyard": u}
        if suite_said:
            m = SUITE_N.search(desc)
            if m:
                n = _n(m.group(1))
            else:
                # suites mentioned without a count: everything above the house units is a suite
                d = DWELLINGS.search(desc)
                base = _n(d.group(1)) if d else (2 if ht == "semi" else None)
                n = u - base if base else 0
            n = min(max(n, 0), max(u - 1, 0))
            if n:
                return {ht: u - n, "suite": n}
    return {ht: u}


def is_house_reissue(r, seen):
    """A second new single-detached house permit at the same address is a re-issue, not a new home."""
    if housing_type(r.get("building_type"), r.get("work_type")) != "single":
        return False
    if "new" not in (r.get("work_type") or "").lower():
        return False
    key = (r.get("address") or "").upper().strip()
    if not key:
        return False
    if key in seen:
        return True
    seen.add(key)
    return False


def first_cluster(events, min_permits=5, window_days=365, radius_m=400):
    """First permit that starts a run of new-home permits: at least min_permits permits adding units
    within window_days and radius_m of it, or a single permit of 20+ units. Skips isolated permits on old rural lots
    (acreage houses, modular homes). Plans where rural lots still saw several permits in a year use
    first_permit_overrides.json."""
    ev = sorted(events)

    def near(a, b):
        if a[4] is None or b[4] is None:
            return True
        return ((a[4] - b[4]) * 111_000) ** 2 + ((a[5] - b[5]) * 66_000) ** 2 <= radius_m ** 2

    for i, e in enumerate(ev):
        d0 = dt.date.fromisoformat(e[0])
        n = 0
        for x in ev[i:]:
            if (dt.date.fromisoformat(x[0]) - d0).days > window_days:
                break
            n += near(e, x)
        if n >= min_permits or e[1] >= 20:
            return e
    return None


def arp_progress(p, t):
    """Measure an area redevelopment plan against the target it states (units, new units or residents)."""
    out = {k: t.get(k) for k in ("measure", "value", "value_high", "value_likely", "baseline_year", "horizon_years", "stated", "stats_bylaw", "adopted", "page", "note", "pop", "pop_year")}
    m = t.get("measure")
    if m == "new_units":
        b = t["baseline_year"]
        done = sum(v["units"] - v["demo"] for y, v in p["permits"].items() if int(y) >= b)
        out.update(current=round(done), progress_pct=round(100 * done / t["value"], 1),
                   current_label=f"net units permitted since {b}")
        if t.get("value_likely"):
            out["progress_likely_pct"] = round(100 * done / t["value_likely"], 1)
    elif m == "pop":
        mid = (t["value"] + t.get("value_high", t["value"])) / 2
        out.update(current=p["pop_2021_est"], progress_pct=round(100 * p["pop_2021_est"] / mid, 1),
                   current_label="residents, 2021 census")
    elif m == "units":
        out.update(current=None, current_label="estimated dwellings today")
    if t.get("pop"):
        out["pop_now"] = p["pop_2021_est"]
    return out


# ---------------------------------------------------------------- build

def main():
    now = dt.datetime.now()
    print("Neighbourhood boundaries")
    nb_rows = soql("neighbourhoods", {})
    nbhds, geoms = {}, {}
    for r in nb_rows:
        if not r.get("geometry_multipolygon"):
            continue
        nid = str(int(num(r["neighbourhood_number"])))
        g = shape(r["geometry_multipolygon"]).buffer(0)
        geoms[nid] = g
        nbhds[nid] = {
            "id": nid,
            "name": r.get("descriptive_name") or r["name"].title(),
            "key": r["name"].upper(),
            "ward": r.get("civic_ward_name"),
            "district": r.get("district"),
            "area_ha": round(area_ha(g), 1),
        }
    name_to_id = {n["key"]: k for k, n in nbhds.items()}
    # Older assessment rolls use names that have since changed
    name_aliases = {
        "OLIVER": "WÎHKWÊNTÔWIN", "RAPPERSWIL": "RAPPERSWILL", "GLENRIDDING AREA": "GLENRIDDING HEIGHTS",
        "TERWILLEGAR SOUTH": "SOUTH TERWILLEGAR", "RIVER VALLEY WINDEMERE": "RIVER VALLEY WINDERMERE",
    }

    def nid_from_name(name):
        key = (name or "").upper().strip()
        key = name_aliases.get(key, key)
        return name_to_id.get(key) or name_to_id.get(re.sub(r" AREA$", "", key))

    nb_ids = list(geoms)
    nb_tree = STRtree([geoms[i] for i in nb_ids])

    def nid_from_point(lon, lat):
        p = Point(lon, lat)
        for i in nb_tree.query(p):
            if geoms[nb_ids[int(i)]].contains(p):
                return nb_ids[int(i)]
        return None

    # --- classification (mode of development-permit classification)
    print("Development permits")
    dp = soql("development_permits", {
        "$select": "neighbourhood_id, neighbourhood_classification, date_extract_y(permit_date) as yr, permit_class, count(*) as n",
        "$group": "neighbourhood_id, neighbourhood_classification, yr, permit_class",
    }, cache_key="dp_grouped")
    cls_votes = defaultdict(lambda: defaultdict(int))
    dp_by = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # nid -> yr -> [total, discretionary]
    for r in dp:
        nid = r.get("neighbourhood_id")
        if not nid or nid not in nbhds:
            continue
        n = int(num(r["n"]))
        c = (r.get("neighbourhood_classification") or "").split(",")[0].strip()
        if c:
            cls_votes[nid][c] += n
        yr = r.get("yr")
        if yr:
            dp_by[nid][yr][0] += n
            if r.get("permit_class") == "Discretionary Development":
                dp_by[nid][yr][1] += n
    for nid, n in nbhds.items():
        votes = cls_votes.get(nid)
        n["class"] = max(votes, key=votes.get) if votes else "Unclassified"
        n["dp"] = {y: v for y, v in sorted(dp_by[nid].items())}

    # --- building permits that change dwelling counts (row level, so we can place them in plan areas)
    print("Building permits")
    bp = soql("building_permits", {
        "$select": "issue_date, year, building_type, work_type, job_description, address, units_added, construction_value, neighbourhood_numberr, latitude, longitude, occupancy_granted_date",
        "$where": "units_added != 0",
        "$order": "row_id",
    }, cache_key="bp_units_v2")

    # --- plans
    print("Statutory plans")
    plan_rows = soql("plans_in_effect", {}, cache_key="plans")
    plans, plan_geoms = [], []
    for i, r in enumerate(plan_rows):
        g = shape(r["geometry"]).buffer(0)
        plans.append({
            "id": i,
            "name": r["plan_in_effect_name"].strip(),
            "type": r["plan_in_effect_type"],
            "type_descr": r["plan_in_effect_type_descr"],
            "bylaw": r.get("bylaw_no"),
            "pdf": r.get("pdf_url"),
            "area_ha": round(area_ha(g), 1),
        })
        plan_geoms.append(g)
    with open(os.path.join(ROOT, "pipeline", "plan_targets.json")) as fh:
        targets_all = json.load(fh)
    for name, t in targets_all.items():
        if name.startswith("_") or t.get("kind") != "repealed" or t["nbhd"] not in geoms:
            continue
        g = geoms[t["nbhd"]]
        plans.append({
            "id": len(plans), "name": name, "type": t["type"],
            "type_descr": "Neighbourhood Area Structure Plan (repealed " + t["repealed"][:4] + ")",
            "bylaw": t.get("stats_bylaw"), "pdf": t.get("pdf"), "area_ha": round(area_ha(g), 1), "repealed": t["repealed"],
        })
        plan_geoms.append(g)

    # neighbourhood <-> plan overlay
    for nid in nbhds:
        nbhds[nid]["plans"] = []
    tree = STRtree(plan_geoms)
    for nid, g in geoms.items():
        gv = g.buffer(0)
        for pi in tree.query(gv):
            pi = int(pi)
            inter = gv.intersection(plan_geoms[pi])
            if inter.is_empty:
                continue
            a = area_ha(inter)
            share_n = a / max(nbhds[nid]["area_ha"], 1e-9)
            share_p = a / max(plans[pi]["area_ha"], 1e-9)
            if share_n < 0.02 and share_p < 0.02:
                continue
            nbhds[nid]["plans"].append({"plan": pi, "share": round(share_n, 3)})
            plans[pi].setdefault("nbhds", []).append({"id": nid, "share": round(share_p, 3)})

    # permits -> neighbourhood and plan aggregates
    TYPES = ["single", "semi", "row", "apartment", "backyard", "suite", "other"]

    def empty_year():
        return {"units": 0, "demo": 0, "value": 0, "occ": 0, **{t: 0 for t in TYPES}}

    nb_permits = defaultdict(lambda: defaultdict(empty_year))
    plan_permits = defaultdict(lambda: defaultdict(empty_year))
    plan_tree = STRtree(plan_geoms)
    unplaced = reissues = 0
    deferred = []
    nb_geo_units = defaultdict(float)
    nb_plan_units = defaultdict(lambda: defaultdict(float))

    def add_permit(targets, u, parts, r):
        for t in targets:
            if u > 0:
                t["units"] += u
                for k, v in parts.items():
                    t[k] += v
                t["value"] += num(r.get("construction_value"))
                if r.get("occupancy_granted_date"):
                    t["occ"] += u
            else:
                t["demo"] += -u

    seen_houses = set()
    nb_events, plan_events = defaultdict(list), defaultdict(list)
    for r in sorted(bp, key=lambda r: r.get("issue_date") or ""):
        u = num(r.get("units_added"))
        yr = r.get("year")
        if not yr:
            continue
        if u > 0 and is_house_reissue(r, seen_houses):
            reissues += 1
            continue
        lat, lon = num(r.get("latitude"), None), num(r.get("longitude"), None)
        # Place by coordinates (handles renamed/split neighbourhoods); fall back to the recorded number
        nid = nid_from_point(lon, lat) if lat and lon else None
        if nid is None:
            nid = str(r.get("neighbourhood_numberr") or "").split(",")[0].split(".")[0].strip()
        parts = split_units(r) if u > 0 else {}
        event = ((r.get("issue_date") or "")[:10], u, (r.get("address") or "").title(), r.get("building_type") or "", lat, lon)
        targets = []
        if nid in nbhds:
            targets.append(nb_permits[nid][yr])
            if u > 0 and event[0]:
                nb_events[nid].append(event)
        plan_hits = []
        if lat and lon:
            p = Point(lon, lat)
            for pi in plan_tree.query(p):
                if plan_geoms[int(pi)].contains(p):
                    plan_hits.append(int(pi))
                    targets.append(plan_permits[int(pi)][yr])
                    if u > 0 and event[0]:
                        plan_events[int(pi)].append(event)
        else:
            unplaced += 1
            if nid in nbhds:
                deferred.append((nid, yr, u, parts, r, event))
        if lat and lon and nid in nbhds and u > 0:
            nb_geo_units[nid] += u
            for pi in plan_hits:
                nb_plan_units[nid][pi] += u
        add_permit(targets, u, parts, r)

    # Permits without coordinates (most permits from 2024 on are not geocoded yet) go to the plans that
    # hold at least 90% of the neighbourhood's geocoded new homes.
    for nid, yr, u, parts, r, event in deferred:
        tot = nb_geo_units[nid]
        targets = []
        for pi, v in nb_plan_units[nid].items():
            if tot and v / tot >= 0.9:
                targets.append(plan_permits[pi][yr])
                if u > 0 and event[0]:
                    plan_events[pi].append(event)
        add_permit(targets, u, parts, r)
    print(f"  {unplaced} unit-changing permits had no coordinates (placed by neighbourhood); {reissues} house re-issues skipped")

    def finalize(d):
        return {y: {k: round(v) for k, v in vals.items()} for y, vals in sorted(d.items())}

    with open(os.path.join(ROOT, "pipeline", "plan_dates.json")) as fh:
        plan_dates = json.load(fh)
    with open(os.path.join(ROOT, "pipeline", "first_permit_overrides.json")) as fh:
        fp_overrides = json.load(fh)

    def first_permit_info(events, override=None):
        if override:
            return {"date": override["date"], "address": override.get("address"), "units": override.get("units"), "override": override.get("note")}
        e = first_cluster(events)
        if not e:
            return None
        big = next((x for x in sorted(events) if x[1] >= 10), None)
        return {"date": e[0], "address": e[2], "units": round(e[1]), "type": e[3], "first_10plus": big[0] if big else None}

    for nid in nbhds:
        nbhds[nid]["permits"] = finalize(nb_permits[nid])
        nbhds[nid]["first_permit"] = first_permit_info(nb_events[nid])
    for pi, p in enumerate(plans):
        events = plan_events[pi]
        if p.get("repealed"):
            # repealed plans use their neighbourhood as the plan area, including permits without coordinates
            nid = targets_all[p["name"]]["nbhd"]
            plan_permits[pi], events = nb_permits[nid], nb_events[nid]
        p["permits"] = finalize(plan_permits[pi])
        p.setdefault("nbhds", [])
        p["first_permit"] = first_permit_info(events, fp_overrides.get(p["name"]))
        d = plan_dates.get(p["name"]) or {}
        p["adopted"], p["adopt_bylaw"], p["adopt_precision"] = d.get("adopted"), d.get("bylaw"), d.get("precision")
        if d.get("alt_start"):
            p["alt_start"] = d["alt_start"]
        # Measurable = the whole build-out falls inside the 2009+ permit data, and building began after adoption
        fp = p["first_permit"]
        early = sum(v["units"] for y, v in p["permits"].items() if int(y) == 2009)
        if not fp:
            p["pace_status"] = "no residential building yet"
        elif fp["date"] < "2010-01-01" or early >= 5:
            p["pace_status"] = "building before 2009 (start not in the data)"
        elif p["adopted"] and fp["date"] < p["adopted"]:
            p["pace_status"] = "building predates the plan"
        else:
            p["pace_status"] = "measurable"
        if p["pace_status"] == "measurable" and p["adopted"]:
            a = dt.date.fromisoformat(p["adopted"] + ("-15" if len(p["adopted"]) == 7 else ""))
            p["lag_years"] = round((dt.date.fromisoformat(fp["date"]) - a).days / 365.25, 2)
            if p.get("alt_start"):
                p["lag_years_alt"] = round((dt.date.fromisoformat(fp["date"]) - dt.date.fromisoformat(p["alt_start"]["date"])).days / 365.25, 2)

    # --- assessment: historical (2012..last year) + current year
    print("Assessments")
    hist = soql("assessment_historical", {
        "$select": "neighbourhood_name, assessment_year, mill_class_1, count(*) as n, sum(assessed_value) as v",
        "$group": "neighbourhood_name, assessment_year, mill_class_1",
    }, cache_key="assess_hist_grouped")
    cur = soql("assessment_current", {
        "$select": "neighbourhood, mill_class_1, count(*) as n, sum(assessed_value) as v",
        "$group": "neighbourhood, mill_class_1",
    }, cache_key="assess_cur_grouped")
    built = soql("assessment_historical", {
        "$select": "neighbourhood_name, year_built, count(*) as n",
        "$where": "assessment_year = 2025 AND mill_class_1 = 'RESIDENTIAL'",
        "$group": "neighbourhood_name, year_built",
    }, cache_key="assess_yearbuilt")
    cur_year = str(now.year)

    assess = defaultdict(lambda: defaultdict(lambda: {"res_n": 0, "res_v": 0, "tot_v": 0, "nonres_v": 0}))

    def add_assess(nid, yr, mill, n, v):
        a = assess[nid][yr]
        a["tot_v"] += v
        if mill in ("RESIDENTIAL", "OTHER RESIDENTIAL"):
            a["res_n"] += n
            a["res_v"] += v
        elif mill == "NON RESIDENTIAL":
            a["nonres_v"] += v

    for r in hist:
        nid = nid_from_name(r.get("neighbourhood_name"))
        if nid:
            add_assess(nid, r["assessment_year"], r.get("mill_class_1"), num(r["n"]), num(r["v"]))
    for r in cur:
        nid = nid_from_name(r.get("neighbourhood"))
        if nid:
            add_assess(nid, cur_year, r.get("mill_class_1"), num(r["n"]), num(r["v"]))
    for nid in nbhds:
        nbhds[nid]["assess"] = {y: {k: round(v) for k, v in a.items()} for y, a in sorted(assess[nid].items())}

    decades = defaultdict(lambda: defaultdict(int))
    for r in built:
        nid = nid_from_name(r.get("neighbourhood_name"))
        y = int(num(r.get("year_built"), 0))
        if nid and y > 1800:
            decades[nid][str(min(max((y // 10) * 10, 1940), 2020))] += int(num(r["n"]))
    for nid in nbhds:
        nbhds[nid]["built"] = dict(sorted(decades[nid].items()))

    # --- census
    print("Census")
    pop16 = soql("census_2016_population", {}, cache_key="pop2016")
    pop19 = soql("census_2019_population", {}, cache_key="pop2019")
    pop21 = soql("census_2021_population", {}, cache_key="pop2021")
    dw16 = soql("census_2016_dwelling_types", {}, cache_key="dw2016")
    dw19 = soql("census_2019_dwelling_status", {}, cache_key="dw2019")
    sf21 = soql("census_2021_short_form", {
        "$where": "characteristics_code in ('1','34','35','36','37','38','39','40','41','42','49')",
    }, cache_key="sf2021")

    bnd16 = soql("census_2016_boundaries", {}, cache_key="bnd2016")
    bnd21 = soql("census_2021_boundaries", {}, cache_key="bnd2021")

    def cid(v):
        return str(int(num(v, -1)))

    # Collect census values keyed by the census-day neighbourhood number.
    # census[year][census_id] = {"pop": .., "dwell": .., "types": {...}}
    census = {y: defaultdict(dict) for y in ("2016", "2019", "2021")}
    for r in pop16:
        census["2016"][cid(r.get("neighbourhood_number"))]["pop"] = num(r.get("total_population"))
    for r in pop21:
        census["2021"][cid(r.get("neighbourhood_number"))]["pop"] = num(r.get("total_population"))
    for r in pop19:
        census["2019"][cid(r.get("neighbourhood_number"))]["pop"] = sum(num(r.get(k)) for k in (
            "woman_girl", "man_boy", "identified_as_another_gender", "prefer_not_to_answer"))
    for r in dw16:
        # "Other attached dwelling" is a subtotal of the attached types — skip it to avoid double counting
        if r.get("response") == "Other attached dwelling":
            continue
        c = census["2016"][cid(r.get("neighbourhood_number"))]
        c["dwell"] = c.get("dwell", 0) + num(r.get("value"))
    for r in dw19:
        census["2019"][cid(r.get("neighbourhood_number"))]["dwell"] = (
            num(r.get("occupied")) + num(r.get("unoccupied")) + num(r.get("refuses_to_answer")))
    code_map = {"35": "single", "36": "semi", "37": "row", "38": "duplex", "39": "apt_low",
                "40": "apt_high", "41": "other", "42": "movable"}
    for r in sf21:
        m = re.search(r"\((\d{4})\)", r.get("geography", ""))
        if not m:
            continue
        c = census["2021"][m.group(1)]
        code, v = r["characteristics_code"], num(r.get("total_gender"))
        if code == "34":
            c["dwell"] = v
        elif code in code_map:
            c.setdefault("types", {})[code_map[code]] = v

    # Census-day boundaries, used to apportion figures for neighbourhoods that were renumbered or split since.
    census_geoms = {"2016": {}, "2021": {}}
    for r in bnd16:
        if r.get("geometry_multipolygon"):
            census_geoms["2016"][cid(r.get("neighbourhood_number"))] = shape(r["geometry_multipolygon"]).buffer(0)
    for r in bnd21:
        if r.get("the_geom"):
            census_geoms["2021"][cid(r.get("neighbourh"))] = shape(r["the_geom"]).buffer(0)
    census_geoms["2019"] = census_geoms["2016"]

    def housing_weight(nid, year):
        """Rough count of dwellings that existed in a neighbourhood before a census year."""
        n = nbhds[nid]
        w = sum(v for d, v in n["built"].items() if int(d) < 2000)
        w += sum(v["units"] - v["demo"] for y, v in n["permits"].items() if int(y) < int(year))
        return max(w, 0)

    for nid in nbhds:
        nbhds[nid]["pop"] = {}
        nbhds[nid]["dwell"] = {}
        nbhds[nid]["census_note"] = None
    for year, recs in census.items():
        direct = {c_id for c_id in recs if c_id in nbhds}
        for c_id in direct:
            vals, n = recs[c_id], nbhds[c_id]
            if "pop" in vals:
                n["pop"][year] = int(vals["pop"])
            if "dwell" in vals:
                n["dwell"][year] = int(vals["dwell"])
            if year == "2021" and "types" in vals:
                n["types_2021"] = {k: int(v) for k, v in vals["types"].items()}
        for c_id, vals in recs.items():
            if c_id in direct:
                continue
            # Retired census neighbourhood: split its totals across today's overlapping neighbourhoods
            g = census_geoms[year].get(c_id)
            if g is None or not (vals.get("pop") or vals.get("dwell")):
                continue
            parts = []
            for i in nb_tree.query(g):
                nid = nb_ids[int(i)]
                if nid in direct:
                    continue
                inter = g.intersection(geoms[nid].buffer(0))
                if inter.is_empty:
                    continue
                frac_of_nb = area_ha(inter) / max(nbhds[nid]["area_ha"], 1e-9)
                if frac_of_nb < 0.05:
                    continue
                parts.append((nid, frac_of_nb, housing_weight(nid, year)))
            # weight by pre-census housing stock when we have it, otherwise by area
            if any(hw for _, _, hw in parts):
                parts = [(nid, f * hw) for nid, f, hw in parts]
            else:
                parts = [(nid, f) for nid, f, _ in parts]
            if not parts:
                continue
            total_w = sum(w for _, w in parts)
            for nid, w in parts:
                share = w / total_w
                n = nbhds[nid]
                for key in ("pop", "dwell"):
                    if key in vals:
                        n[key][year] = int(n[key].get(year, 0) + vals[key] * share)
                if year == "2021" and "types" in vals:
                    t = n.setdefault("types_2021", {})
                    for k, v in vals["types"].items():
                        t[k] = int(t.get(k, 0) + v * share)
                n["census_note"] = "Some census figures apportioned from former neighbourhood boundaries"

    # --- derived metrics
    for nid, n in nbhds.items():
        ha = max(n["area_ha"], 1e-9)
        p = n["pop"]
        n["density_pop_ha"] = round(p["2021"] / ha, 2) if p.get("2021") else None
        d21 = n["dwell"].get("2021")
        n["density_du_ha"] = round(d21 / ha, 2) if d21 else None
        # units permitted since the 2021 census (May 2021) — permits from June 2021 are approximated as 2021 H2 + later years
        since = sum(v["units"] - v["demo"] for y, v in n["permits"].items() if int(y) >= 2022)
        since += 0.6 * (n["permits"].get("2021", {}).get("units", 0) - n["permits"].get("2021", {}).get("demo", 0))
        n["units_since_2021"] = round(since)
        # neighbourhoods with no 2021 census record but an empty 2019 count (e.g. Blatchford) start from zero
        base = d21 if d21 is not None else (0 if n["dwell"].get("2019", 0) == 0 and since > 0 else None)
        if not base and base is not None:
            # nothing counted at the census, so every unit permitted to date is new stock
            since = sum(v["units"] - v["demo"] for v in n["permits"].values())
        n["dwell_est_now"] = max(round(base + since), 0) if base is not None else None
        n["density_du_ha_est"] = round(n["dwell_est_now"] / ha, 2) if n["dwell_est_now"] else None

    # plan level census (area-weighted from neighbourhoods)
    for p in plans:
        pop = dw = 0.0
        for link in p["nbhds"]:
            n = nbhds[link["id"]]
            share_n = next((x["share"] for x in n["plans"] if x["plan"] == p["id"]), 0)
            # a plan covering most of a neighbourhood holds essentially all of its homes (the rest is ravine, roads, etc.)
            if share_n >= 0.75:
                share_n = 1.0
            pop += share_n * n["pop"].get("2021", 0)
            dw += share_n * n["dwell"].get("2021", 0)
        p["pop_2021_est"] = round(pop)
        p["dwell_2021_est"] = round(dw)
        # An area that already had a few hundred homes before its first permit in the data was built
        # (or redeveloping) before 2009, so its start can't be timed from permits.
        if p.get("pace_status") == "measurable" and not (p.get("first_permit") or {}).get("override"):
            permitted = sum(v["units"] - v["demo"] for y, v in p["permits"].items() if int(y) <= 2020)
            permitted += 0.4 * (p["permits"].get("2021", {}).get("units", 0) - p["permits"].get("2021", {}).get("demo", 0))
            if dw - permitted > 300:
                p["pace_status"] = "homes existed before 2009 (start not in the data)"
                p.pop("lag_years", None)
                p.pop("lag_years_alt", None)

    # approved buildout statistics transcribed from each plan's Land Use & Population Statistics table
    with open(os.path.join(ROOT, "pipeline", "plan_targets.json")) as fh:
        targets = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    for p in plans:
        t = targets.get(p["name"])
        if t and t.get("kind") == "arp":
            p["arp"] = arp_progress(p, t)
            t = {**t, "units": t["value"]} if t.get("measure") == "units" else None
        p["target"] = t
        permits = p["permits"]
        since = sum(v["units"] - v["demo"] for y, v in permits.items() if int(y) >= 2022)
        since += 0.6 * (permits.get("2021", {}).get("units", 0) - permits.get("2021", {}).get("demo", 0))
        if p["dwell_2021_est"] == 0:
            since = sum(v["units"] - v["demo"] for v in permits.values())
        p["dwell_est_now"] = max(round(p["dwell_2021_est"] + since), 0)
        recent = [v for y, v in permits.items() if int(y) >= 2015]
        tot = sum(v["units"] for v in recent)
        p["recent_ldr_pct"] = round(100 * sum(v["single"] + v["semi"] for v in recent) / tot, 1) if tot else None
        p["recent_units"] = round(tot)
        if p.get("arp") and p["arp"]["measure"] == "units":
            p["arp"].update(current=p["dwell_est_now"], progress_pct=round(100 * p["dwell_est_now"] / p["arp"]["value"], 1))
        if t and t.get("units"):
            p["buildout_pct"] = round(100 * p["dwell_est_now"] / t["units"], 1)
            p["remaining_units"] = max(t["units"] - p["dwell_est_now"], 0)
            if t.get("nra_ha"):
                p["achieved_upnrha"] = round(p["dwell_est_now"] / t["nra_ha"], 1)

    for nid, n in nbhds.items():
        best = None
        for link in n["plans"]:
            p = plans[link["plan"]]
            if p.get("target") and p["target"].get("units") and link["share"] >= 0.5:
                if best is None or link["share"] > best[1]:
                    best = (p["id"], link["share"])
        n["primary_plan"] = best[0] if best else None

    out = {
        "generated": now.isoformat(timespec="minutes"),
        "current_year": now.year,
        "sources": {k: f"https://data.edmonton.ca/d/{v}" for k, v in DATASETS.items()},
        "neighbourhoods": list(nbhds.values()),
        "plans": plans,
    }
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "growth.json"), "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    with open(os.path.join(DATA, "neighbourhoods.geojson"), "w") as fh:
        json.dump({"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"id": nid}, "geometry": simplified(g)}
            for nid, g in geoms.items()]}, fh, separators=(",", ":"))
    with open(os.path.join(DATA, "plans.geojson"), "w") as fh:
        json.dump({"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"id": i}, "geometry": simplified(g)}
            for i, g in enumerate(plan_geoms)]}, fh, separators=(",", ":"))
    print("Wrote data/growth.json,", len(nbhds), "neighbourhoods,", len(plans), "plans")


if __name__ == "__main__":
    main()
