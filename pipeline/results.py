"""Area-by-area U.S. House results for a district.

1. If overrides/district_results/{code}.json exists, use it (hand-checked official results).
2. Otherwise use OpenElections (openelections.net), a free public project that collects official
   results from state and local election offices, and add them up by county.
Coverage differs by state; districts without results are marked as unavailable.
"""
import csv
import io
import json
import logging
import os
import re

from .core import ROOT

log = logging.getLogger("solon")
GH_API = "https://api.github.com/repos/openelections/openelections-data-{st}/contents/{path}"


def norm_county(s):
    s = (s or "").lower().replace("saint ", "st ").replace("ste.", "ste").replace("st.", "st")
    s = re.sub(r"\b(county|parish|borough|city and borough|census area|municipality)\b", "", s)
    return re.sub(r"[^a-z]", "", s)


def is_house_office(office):
    o = (office or "").lower()
    return ("house" in o or "representative" in o or "congress" in o) and "state" not in o


def party_of(p):
    p = (p or "").upper()
    if p.startswith("DEM") or p in ("D", "DFL"):
        return "D"
    if p.startswith("REP") or p == "R":
        return "R"
    return "O"


def override(code):
    p = ROOT / "overrides" / "district_results" / f"{code}.json"
    return json.load(open(p, encoding="utf-8")) if p.exists() else None


def list_files(http, st, year):
    files = []
    for path in (str(year), f"{year}/counties"):
        headers = {"Accept": "application/vnd.github+json"}
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
        r = http.get(GH_API.format(st=st.lower(), path=path), allow_404=True, headers=headers)
        if r is None:
            continue
        files += [x for x in r.json() if x.get("type") == "file"]
    return files


def tally_rows(rows, district, kind):
    by_county, cand = {}, {}
    for row in rows:
        r = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        if not is_house_office(r.get("office")):
            continue
        try:
            if int(float(r.get("district") or -1)) != district:
                continue
        except ValueError:
            continue
        if kind == "precinct" and "total" in (r.get("precinct") or "").lower():
            continue
        try:
            v = int(float((r.get("votes") or "0").replace(",", "")))
        except ValueError:
            continue
        c = by_county.setdefault(r.get("county") or "", {"D": 0, "R": 0, "O": 0})
        pty = party_of(r.get("party"))
        c[pty] += v
        cand[(r.get("candidate"), pty)] = cand.get((r.get("candidate"), pty), 0) + v
    return by_county, cand


def from_openelections(http, st, district, election_date):
    year = election_date[:4]
    files = list_files(http, st, year)
    pick = [f for f in files if election_date in f["name"] and "general" in f["name"] and f["name"].endswith(".csv")]
    county_files = [f for f in pick if "__county" in f["name"]]
    precinct = [f for f in pick if "__precinct" in f["name"]]
    statewide_precinct = [f for f in precinct if f["name"].count("__") == 3]
    for kind, chosen in (("county", county_files[:1]), ("precinct", statewide_precinct[:1]),
                         ("precinct", [f for f in precinct if f not in statewide_precinct])):
        if not chosen:
            continue
        by_county, cand = {}, {}
        for f in chosen:
            text = http.get(f["download_url"], timeout=300).text
            bc, cd = tally_rows(csv.DictReader(io.StringIO(text)), district, kind)
            for k, v in bc.items():
                t = by_county.setdefault(k, {"D": 0, "R": 0, "O": 0})
                for p in "DRO":
                    t[p] += v[p]
            for k, v in cd.items():
                cand[k] = cand.get(k, 0) + v
        if by_county:
            return {"by_county": by_county, "candidates": cand,
                    "files": [f["download_url"] for f in chosen]}
    return None


def pct(d, r, o):
    n = d + r + o
    return (round(100 * d / n), round(100 * r / n)) if n else (None, None)


def district_results(http, cfg, code, county_list):
    ov = override(code)
    if ov:
        return ov
    st, num = code.split("-")
    district = 1 if num == "AL" else int(num)
    try:
        oe = from_openelections(http, st, district, cfg["districts"]["results_election_date"])
    except Exception as e:
        log.warning("OpenElections read failed for %s: %s", code, e)
        oe = None
    if not oe:
        return None
    names = {norm_county(c["county"]): c for c in county_list}
    areas = []
    D = R = O = 0
    for cname, t in oe["by_county"].items():
        D, R, O = D + t["D"], R + t["R"], O + t["O"]
        c = names.get(norm_county(cname))
        d, r = pct(t["D"], t["R"], t["O"])
        areas.append({"name": (c["county"] if c else cname), "fips": c["fips"] if c else None,
                      "d": d, "r": r, "votes": t})
    areas.sort(key=lambda a: -sum(a["votes"].values()))
    d, r = pct(D, R, O)
    return {"election": cfg["districts"]["results_election_date"], "level": "county",
            "whole": {"d": d, "r": r, "votes": {"D": D, "R": R, "O": O}},
            "areas": areas,
            "candidates": [{"candidate": k[0], "party": k[1], "votes": v}
                           for k, v in sorted(oe["candidates"].items(), key=lambda x: -x[1])],
            "source": "Official results compiled by OpenElections, added up by county",
            "source_files": oe["files"]}
