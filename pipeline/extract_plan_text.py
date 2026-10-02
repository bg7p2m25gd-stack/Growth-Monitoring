"""Download statutory plan PDFs (Plans in Effect) and extract their text for review."""
import concurrent.futures as cf
import json
import logging
import os
import urllib.parse

import requests
from pypdf import PdfReader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_DIR = os.path.join(ROOT, "cache", "plans")
TXT_DIR = os.path.join(ROOT, "cache", "plans_txt")
PLANS_URL = "https://data.edmonton.ca/resource/jdmr-bidz.json?$select=bylaw_no,plan_in_effect_name,plan_in_effect_type,pdf_url&$limit=500"

logging.disable(logging.CRITICAL)


def pdf_path(url):
    return os.path.join(PDF_DIR, urllib.parse.unquote(os.path.basename(url)).replace("'", ""))


def download(url):
    f = pdf_path(url)
    if not (os.path.exists(f) and os.path.getsize(f) > 10000):
        r = requests.get(url, timeout=180)
        r.raise_for_status()
        with open(f, "wb") as fh:
            fh.write(r.content)
    return f


def extract(f):
    out = os.path.join(TXT_DIR, os.path.basename(f)[:-4] + ".txt")
    if os.path.exists(out):
        return out
    parts = []
    for i, p in enumerate(PdfReader(f).pages):
        try:
            parts.append(f"\n<<PAGE {i + 1}>>\n" + (p.extract_text() or ""))
        except Exception:
            parts.append(f"\n<<PAGE {i + 1} ERR>>\n")
    with open(out, "w") as fh:
        fh.write("".join(parts))
    return out


def main():
    os.makedirs(PDF_DIR, exist_ok=True)
    os.makedirs(TXT_DIR, exist_ok=True)
    plans = requests.get(PLANS_URL, timeout=60).json()
    urls = [p["pdf_url"] for p in plans if p.get("pdf_url")]
    with cf.ThreadPoolExecutor(8) as ex:
        files = list(ex.map(download, urls))
    with cf.ProcessPoolExecutor(6) as ex:
        for out in ex.map(extract, files):
            print("ok", os.path.basename(out))


if __name__ == "__main__":
    main()
