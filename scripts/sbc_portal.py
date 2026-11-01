"""Catalog and download Elevance Health Summary of Benefits and Coverage (SBC) PDFs.

Sources:
  - https://sbc.anthem.com/dps/    Anthem / Anthem Blue Cross / Anthem BCBS / Empire (14 Blue states)
  - https://sbc.wellpoint.com/dps/ Wellpoint (FL, MD, TX, WA)
  - CMS Exchange Plan Attributes PUF (HealthCare.gov states): adds SBC links the portal search does not
    return, and HIOS plan IDs for joining with CMS rate/benefit data later.

Usage:
  python scripts/sbc_portal.py catalog                 # query portals -> data/catalog/sbc_catalog_<year>.csv
  python scripts/sbc_portal.py download [--markets ...] # fetch PDFs listed in the catalog (resumable)
  python scripts/sbc_portal.py manifest                # hash/verify files -> data/catalog/sbc_manifest_<year>.csv
"""

import argparse
import csv
import hashlib
import io
import re
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) plan-info-chatbot/0.1"}

PORTALS = {
    "anthem": {
        "host": "sbc.anthem.com",
        "states": ["CA", "CO", "CT", "GA", "IN", "KY", "ME", "MO", "NH", "NV", "NY", "OH", "VA", "WI"],
        "markets": ["Individual", "Small Group", "Large Group", "MEWA", "ABF", "Student Health", "Large Group Custom"],
        "extra_form": {},
    },
    "wellpoint": {
        "host": "sbc.wellpoint.com",
        "states": ["FL", "MD", "TX", "WA"],
        "markets": ["Individual"],
        # The Wellpoint site filters results by this hidden field; without it every search returns 0 rows.
        "extra_form": {"anthemLogoImageName": "wellpointLogo.png"},
    },
}

CATALOG_FIELDS = [
    "source", "portal", "brand", "state", "market_unit", "plan_name", "plan_type", "network", "exchange",
    "plan_status", "rx_rider", "coverage_period", "contract_code", "sbc_id", "doc_sys_id", "hios_plan_ids",
    "language", "source_system", "dps_unique_id", "fetch_method", "url", "local_path",
]
HOST_TO_PORTAL = {cfg["host"]: name for name, cfg in PORTALS.items()}
PUF_URL = "https://download.cms.gov/marketplace-puf/{year}/plan-attributes-puf.zip"
DEEPLINK_RE = re.compile(r"/deepLink/[^/]*/([0-9A-Z]+)/(\w+)/DG(\d+)\.pdf$")


def slug(text, maxlen=90):
    s = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-")
    return s[:maxlen].rstrip("-") or "plan"


def catalog_path(year):
    return DATA / "catalog" / f"sbc_catalog_{year}.csv"


def search_portal(portal, state, market, eff_date, language, retries=4):
    # The portal throws sporadic 500s; a fresh session usually succeeds.
    for attempt in range(retries):
        try:
            return _search_portal(portal, state, market, eff_date, language)
        except (requests.RequestException, ValueError) as e:
            if attempt == retries - 1:
                raise
            print(f"  retry {portal} {state} {market}: {e!r}", file=sys.stderr)
            time.sleep(3 * 2 ** attempt)


def _search_portal(portal, state, market, eff_date, language):
    cfg = PORTALS[portal]
    host = cfg["host"]
    s = requests.Session()
    s.headers.update(UA)
    s.get(f"https://{host}/dps/", timeout=30)
    s.post(f"https://{host}/dps/searchPage",
           data={"stateCode": state, "sdbcFlag": "false", "marketUnit": market}, timeout=30)
    form = {
        "draw": "1", "sortedColumns": "planName", "sortedDir": "asc", "startPageValue": "0", "recordPerPage": "10000",
        "searchType": "", "sbcValue": "", "sbcId": "", "contractCode": "", "planName": "",
        "coverageEffectiveDate": eff_date, "language": language, "marketUnit": market, "state": state,
        **cfg["extra_form"],
    }
    r = s.post(f"https://{host}/dps/search/datas", data=form,
               headers={"X-Requested-With": "XMLHttpRequest"}, timeout=180)
    r.raise_for_status()
    j = r.json()
    rows = j.get("data") or []
    if len(rows) != j.get("recordsTotal", len(rows)):
        print(f"  WARN {portal} {state} {market}: got {len(rows)} of {j.get('recordsTotal')}", file=sys.stderr)
    return rows


def local_path(year, portal, state, market, name, code, sbc_id):
    return str(Path("data/raw/sbc") / year / portal / state / slug(market) / f"{slug(name)}__{code}__{sbc_id}.pdf")


def load_puf_sbcs(year):
    """Elevance rows from the CMS Plan Attributes PUF, keyed by (host, sbc_id)."""
    dest = DATA / "reference" / f"cms_puf_{year}" / "plan-attributes-puf.csv"
    if not dest.exists():
        r = requests.get(PUF_URL.format(year=year), headers=UA, timeout=300)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            dest.write_bytes(z.read(name))
    found = {}
    with dest.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            url = r["URLForSummaryofBenefitsCoverage"].strip()
            host = urlparse(url).netloc.lower()
            m = DEEPLINK_RE.search(url)
            if host not in HOST_TO_PORTAL or not m:
                continue
            code, lang, sbc_id = m.groups()
            entry = found.setdefault((host, sbc_id), {"row": r, "url": url, "code": code, "lang": lang, "hios": set()})
            entry["hios"].add(r["PlanId"])
    return found


def build_catalog(year, language):
    eff_date = f"01/01/{year}"
    out, seen = [], set()
    for portal, cfg in PORTALS.items():
        for state in cfg["states"]:
            for market in cfg["markets"]:
                rows = search_portal(portal, state, market, eff_date, language)
                print(f"{portal:9s} {state} {market:20s} {len(rows):5d}", flush=True)
                for r in rows:
                    key = (cfg["host"], r["sbcId"])
                    if key in seen:
                        continue
                    seen.add(key)
                    name = r.get("planName") or ""
                    code = r["contractCode"]
                    if r.get("sourceSystem") == "EPDG":
                        # EPDG docs (grandfathered pre-2014 group plans) have no deep link: a DG<docSysId> link
                        # returns an unrelated 2022 SBC. They are only served by the portal's previewDocument POST.
                        fetch_method, url = "preview_post", f"https://{cfg['host']}/dps/"
                    else:
                        # The first path segment is cosmetic; the portal resolves the doc from contract code + DG<sbcId>.
                        fetch_method = "deeplink"
                        url = (f"https://{cfg['host']}/dpsdeeplink/deepLink/{slug(name, 120).replace('-', '')}"
                               f"/{code}/{language}/DG{r['sbcId']}.pdf")
                    out.append({
                        "source": "portal", "portal": portal, "brand": r.get("brand"), "state": state,
                        "market_unit": market, "plan_name": name, "plan_type": r.get("planType"),
                        "network": (r.get("networkID") or "").strip(), "exchange": r.get("excngPlnId"),
                        "plan_status": r.get("planStatus"), "rx_rider": r.get("rxRider"),
                        "coverage_period": r.get("coveragePeriod"), "contract_code": code, "sbc_id": r["sbcId"],
                        "doc_sys_id": r.get("docSysId"), "hios_plan_ids": "", "language": r.get("language"),
                        "source_system": r.get("sourceSystem"), "dps_unique_id": r.get("dpsUniqueId"),
                        "fetch_method": fetch_method, "url": url,
                        "local_path": local_path(year, portal, state, market, name, code, r["sbcId"]),
                    })
                time.sleep(0.5)

    puf = load_puf_sbcs(year)
    by_key = {(PORTALS[r["portal"]]["host"], r["sbc_id"]): r for r in out}
    added = 0
    for key, e in puf.items():
        hios = ";".join(sorted(e["hios"]))
        if key in by_key:
            by_key[key]["hios_plan_ids"] = hios
            continue
        if e["lang"] != language:
            continue
        p = e["row"]
        portal = HOST_TO_PORTAL[key[0]]
        market = "Small Group" if p["MarketCoverage"].startswith("SHOP") else p["MarketCoverage"]
        out.append({
            "source": "cms_puf", "portal": portal, "brand": p["IssuerMarketPlaceMarketingName"], "state": p["StateCode"],
            "market_unit": market, "plan_name": p["PlanMarketingName"], "plan_type": p["PlanType"], "network": "",
            "exchange": "On", "plan_status": "", "rx_rider": "", "coverage_period": f"01/01/{year} - 12/31/{year}",
            "contract_code": e["code"], "sbc_id": key[1], "doc_sys_id": "", "hios_plan_ids": hios,
            "language": language, "source_system": "DOCGEN", "dps_unique_id": "", "fetch_method": "deeplink",
            "url": e["url"],
            "local_path": local_path(year, portal, p["StateCode"], market, p["PlanMarketingName"], e["code"], key[1]),
        })
        added += 1
    print(f"CMS PUF: {len(puf)} Elevance SBC links, {added} not returned by portal search -> added")

    path = catalog_path(year)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CATALOG_FIELDS)
        w.writeheader()
        w.writerows(out)
    print(f"\n{len(out)} documents -> {path.relative_to(ROOT)}")


def read_catalog(year):
    with catalog_path(year).open() as f:
        return list(csv.DictReader(f))


_local = threading.local()


def _session():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers.update(UA)
    return _local.s


def _portal_session(row):
    """Session that has opened the portal search page for this row's state/market (previewDocument needs it)."""
    key = (row["portal"], row["state"], row["market_unit"])
    if getattr(_local, "portal_key", None) != key:
        host = PORTALS[row["portal"]]["host"]
        s = requests.Session()
        s.headers.update(UA)
        s.get(f"https://{host}/dps/", timeout=30)
        s.post(f"https://{host}/dps/searchPage",
               data={"stateCode": row["state"], "sdbcFlag": "false", "marketUnit": row["market_unit"]}, timeout=30)
        _local.portal_s, _local.portal_key = s, key
    return _local.portal_s


def _preview_post(row):
    # Mirrors the form the portal's download button submits (SBCSearch.js downloadPopup).
    host = PORTALS[row["portal"]]["host"]
    form = {
        "contractCode": row["contract_code"], "planName": row["plan_name"], "state": row["state"],
        "planType": row["plan_type"], "marketUnit": row["market_unit"], "coverageEffectiveDate": row["coverage_period"],
        "exchangeIndicator": "undefined", "networkName": row["network"], "language": row["language"],
        "planStatus": row["plan_status"] or "null", "docSysId": row["doc_sys_id"], "sbcId": row["sbc_id"],
        "dpsUniqueId": row["dps_unique_id"], "brand": row["brand"], "productId": "-", "replicationFlag": "OFF",
        "epdgReplicationFlag": "OFF", "total": "1", "flag": "download", "sourceSystem": row["source_system"],
        "docType": "SBC",
    }
    return _portal_session(row).post(f"https://{host}/dps/search/previewDocument", data=form, timeout=120)


def is_pdf(path):
    try:
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


def fetch_one(row, retries=4):
    dest = ROOT / row["local_path"]
    if dest.exists() and is_pdf(dest) and dest.stat().st_size > 10_000:
        return "skip", row
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    err = ""
    for attempt in range(retries):
        try:
            if row["fetch_method"] == "preview_post":
                r = _preview_post(row)
            else:
                r = _session().get(row["url"], timeout=120)
            if r.status_code == 200 and r.content[:5] == b"%PDF-":
                tmp.write_bytes(r.content)
                tmp.replace(dest)
                return "ok", row
            err = f"HTTP {r.status_code} {r.headers.get('content-type')}"
        except requests.RequestException as e:
            err = repr(e)
        _local.__dict__.pop("portal_key", None)
        time.sleep(2 ** attempt)
    tmp.unlink(missing_ok=True)
    return f"fail: {err}", row


def download(year, markets, portals, workers):
    rows = [r for r in read_catalog(year)
            if (not markets or r["market_unit"] in markets) and (not portals or r["portal"] in portals)]
    # Individual plans first: they are the consumer-facing set and the most useful for the chatbot.
    # Grouping by state/market also lets each worker reuse its portal session for previewDocument fetches.
    order = {"Individual": 0, "Small Group": 1}
    rows.sort(key=lambda r: (order.get(r["market_unit"], 2), r["portal"], r["state"], r["market_unit"]))
    print(f"{len(rows)} documents to check/download with {workers} workers")
    counts, failures = {"ok": 0, "skip": 0, "fail": 0}, []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fetch_one, r) for r in rows]
        for i, fut in enumerate(as_completed(futs), 1):
            status, row = fut.result()
            key = status.split(":")[0]
            counts[key] += 1
            if key == "fail":
                failures.append((row, status))
            if i % 250 == 0 or i == len(rows):
                print(f"  {i}/{len(rows)} {counts}", flush=True)
    fail_path = DATA / "catalog" / f"sbc_failures_{year}.csv"
    fail_path.unlink(missing_ok=True)
    if failures:
        with fail_path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["url", "local_path", "error"])
            for row, status in failures:
                w.writerow([row["url"], row["local_path"], status])
        print(f"{len(failures)} failures -> {fail_path.relative_to(ROOT)}")


def manifest(year):
    rows = read_catalog(year)
    out = []
    for r in rows:
        p = ROOT / r["local_path"]
        ok = p.exists() and is_pdf(p)
        out.append({**r, "downloaded": ok, "bytes": p.stat().st_size if ok else "",
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest() if ok else ""})
    path = DATA / "catalog" / f"sbc_manifest_{year}.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    got = sum(1 for o in out if o["downloaded"])
    size = sum(int(o["bytes"]) for o in out if o["downloaded"])
    print(f"{got}/{len(out)} downloaded, {size / 1e9:.2f} GB -> {path.relative_to(ROOT)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["catalog", "download", "manifest"])
    ap.add_argument("--year", default="2027", help="plan year; searches documents effective 01/01/<year>")
    ap.add_argument("--language", default="English", choices=["English", "Spanish"])
    ap.add_argument("--markets", nargs="*", help="limit download to these market units, e.g. Individual 'Small Group'")
    ap.add_argument("--portals", nargs="*", choices=list(PORTALS), help="limit download to these portals")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    if a.command == "catalog":
        build_catalog(a.year, a.language)
    elif a.command == "download":
        download(a.year, a.markets, a.portals, a.workers)
    else:
        manifest(a.year)


if __name__ == "__main__":
    main()
