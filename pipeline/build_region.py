"""
Municipal boundaries for the Edmonton region (CMA 835), for the map on the Housing starts (CMHC) tab.

Each census subdivision in the region is tagged with the key build_cmhc.py uses for it. The small towns,
villages, summer villages and First Nations that CMHC data only covers as a group get the key "other".
Boundaries change rarely, so this is run by hand when a new census is published, not in the weekly refresh.

Source: Statistics Canada, 2021 Census cartographic boundary files (census subdivisions and census
metropolitan areas), Open Government Licence - Canada. Writes data/region.geojson.

    .venv/bin/python pipeline/build_region.py
"""
import json
import os
import sys

import requests
from shapely.geometry import MultiPolygon, mapping, shape
from shapely.geometry.polygon import orient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_cmhc import GEOS, OTHER  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "region.geojson")
BASE = "https://geo.statcan.gc.ca/geo_wa/rest/services/2021/Cartographic_boundary_files/MapServer"
CMA = "835"
SIMPLIFY = 0.0008  # degrees, about 60 m


def query(layer, **params):
    p = {"outSR": "4326", "maxAllowableOffset": str(SIMPLIFY), "f": "geojson", **params}
    r = requests.get(f"{BASE}/{layer}/query", params=p, timeout=120)
    r.raise_for_status()
    d = r.json()
    if "features" not in d:
        raise ValueError(f"StatCan query failed: {d}")
    return d["features"]


def rounded(geom):
    # d3 draws on the sphere and wants exterior rings clockwise (the reverse of RFC 7946)
    geom = MultiPolygon([orient(p, -1.0) for p in geom.geoms]) if geom.geom_type == "MultiPolygon" else orient(geom, -1.0)
    def rnd(c):
        return [rnd(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], 4), round(c[1], 4)]
    g = mapping(geom)
    return {"type": g["type"], "coordinates": rnd(g["coordinates"])}


def main():
    cma = shape(query(6, where=f"CMAUID='{CMA}'", outFields="CMAUID")[0]["geometry"]).buffer(0)
    w, s, e, n = cma.bounds
    csds = query(9, where="PRUID='48'", geometry=f"{w},{s},{e},{n}", geometryType="esriGeometryEnvelope", inSR="4326",
                 spatialRel="esriSpatialRelIntersects", outFields="CSDUID,CSDNAME,CSDTYPE,LANDAREA")
    by_code = {gid: k for k, (gid, gtype, _) in GEOS.items() if gtype == "4"}
    feats = []
    for f in csds:
        g = shape(f["geometry"]).buffer(0)
        if g.is_empty or g.intersection(cma).area / g.area < 0.5:
            continue
        p = f["properties"]
        key = by_code.get(p["CSDUID"], OTHER[0])
        feats.append({"type": "Feature", "geometry": rounded(g),
                      "properties": {"key": key, "csduid": p["CSDUID"], "name": p["CSDNAME"], "csdtype": p["CSDTYPE"],
                                     "land_km2": round(p["LANDAREA"], 1)}})
    missing = set(by_code) - {f["properties"]["csduid"] for f in feats}
    if missing:
        sys.exit(f"No boundary found for {sorted(missing)}")
    feats.sort(key=lambda f: (f["properties"]["key"] == OTHER[0], f["properties"]["name"]))
    with open(OUT, "w") as fh:
        json.dump({"type": "FeatureCollection", "source": "Statistics Canada, 2021 Census cartographic boundary files",
                   "features": feats}, fh, separators=(",", ":"))
    print(f"Wrote {OUT}: {len(feats)} census subdivisions, {os.path.getsize(OUT) // 1024} KB")


if __name__ == "__main__":
    main()
