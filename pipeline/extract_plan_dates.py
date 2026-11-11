"""
One-off helper: read each plan's adopting bylaw and date from the bylaw history at the front of its
office consolidation (cache/plans_txt, made by extract_plan_text.py) and write pipeline/plan_dates.json.

plan_dates.json is the committed source of truth. Re-running this keeps any entry marked "verified"
and only fills in plans that are missing, so hand corrections are never overwritten.
"""
import json
import os
import re

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TXT = os.path.join(ROOT, "cache", "plans_txt")
OUT = os.path.join(ROOT, "pipeline", "plan_dates.json")
PLANS_URL = "https://data.edmonton.ca/resource/jdmr-bidz.json?$select=plan_in_effect_name,pdf_url&$limit=500"

MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"])}
MONTHS.update({"sept": 9, "sep": 9, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "oct": 10, "nov": 11, "dec": 12})

# "Bylaw 15514 Approved July 19, 2010 (to adopt ..." is the standard first line of the bylaw history
ADOPT = re.compile(r"Bylaw\s*(\d{4,5})\s+(?:Approved|Adopted)\s+(?:on\s+)?([A-Z][a-z]+\.?)\s+(\d{1,2}),?\s+(\d{4})\s*\(\s*to\s+(?:adopt|approve)", re.I)
# fallback: "Bylaw 15037 was adopted by Council in April 2009" (month only)
ADOPT_MONTH = re.compile(r"Bylaw\s*(\d{4,5})[^.]{0,60}?(?:was\s+)?(?:adopted|approved)[^.]{0,30}?(?:on|in)\s+([A-Z][a-z]+\.?)\s*(?:(\d{1,2}),?\s*)?(\d{4})", re.I)


def iso(mon, day, year):
    m = MONTHS.get(mon.lower().rstrip("."))
    if not m:
        return None, None
    return (f"{year}-{m:02d}-{int(day):02d}", "day") if day else (f"{year}-{m:02d}", "month")


def main():
    existing = json.load(open(OUT)) if os.path.exists(OUT) else {}
    plans = requests.get(PLANS_URL, timeout=60).json()
    out = {k: v for k, v in existing.items() if k.startswith("_")}
    for p in plans:
        name = p["plan_in_effect_name"].strip()
        if existing.get(name, {}).get("verified"):
            out[name] = existing[name]
            continue
        base = os.path.basename(p.get("pdf_url") or "").replace("%20", " ").replace("'", "")[:-4]
        path = os.path.join(TXT, base + ".txt")
        entry = {"bylaw": None, "adopted": None, "precision": None, "source": "plan consolidation bylaw history"}
        if os.path.exists(path):
            t = re.sub(r"\s+", " ", open(path).read()[:20000])
            m = ADOPT.search(t)
            if m:
                entry["bylaw"] = m.group(1)
                entry["adopted"], entry["precision"] = iso(m.group(2), m.group(3), m.group(4))
            else:
                m = ADOPT_MONTH.search(t)
                if m:
                    entry["bylaw"] = m.group(1)
                    entry["adopted"], entry["precision"] = iso(m.group(2), m.group(3), m.group(4))
        out[name] = {**entry, **{k: v for k, v in existing.get(name, {}).items() if k == "note"}}
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    print(f"wrote {OUT}: {sum(1 for k, v in out.items() if not k.startswith('_') and v.get('adopted'))} dated")


if __name__ == "__main__":
    main()
