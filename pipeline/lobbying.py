"""Groups that lobbied on a bill, from Lobbying Disclosure Act reports (lda.gov), and the
federal PACs connected to those groups (FEC committee file).

A report counts only if its lobbying-issue text names the bill number. Reports say which
bills a group lobbied on, not which side it took, so Solon Watch never labels a side.
"""
import logging
import os
import re
import time
from pathlib import Path

import yaml

from .core import ROOT, Budget, bill_mention_regex, norm_name, out_path, read_json, write_json

log = logging.getLogger("solon")
API = "https://lda.gov/api/v1/filings/"
PAC_TYPES = {"Q", "N"}          # qualified and non-qualified PACs
PAC_DESIGNATIONS = {"B", "U"}   # lobbyist/registrant PAC, unauthorized


def variants(btype, number):
    base = {"hr": ["H.R.", "HR", "H.R"], "s": ["S.", "S"], "hjres": ["H.J.Res.", "HJRes"],
            "sjres": ["S.J.Res.", "SJRes"], "hres": ["H.Res.", "HRes"], "sres": ["S.Res.", "SRes"],
            "hconres": ["H.Con.Res."], "sconres": ["S.Con.Res."]}[btype]
    out = []
    for b in base:
        out += [f"{b} {number}", f"{b}{number}"]
    return list(dict.fromkeys(out))


def client_name(raw):
    """'FIRM OBO CLIENT' and 'FIRM ON BEHALF OF CLIENT' mean the real client is after OBO."""
    m = re.split(r"\b(?:OBO|O/B/O|ON BEHALF OF)\b", raw or "", flags=re.I)
    return m[-1].strip(" ,.-") if len(m) > 1 else (raw or "").strip()


def amount(f):
    for k in ("income", "expenses"):
        try:
            if f.get(k) not in (None, ""):
                return float(f[k])
        except (TypeError, ValueError):
            pass
    return None


def search_bill(http, cfg, btype, number):
    rx = bill_mention_regex(btype, number)
    headers = {}
    if os.environ.get("LDA_API_KEY"):
        headers["Authorization"] = "Token " + os.environ["LDA_API_KEY"]
    seen, filings = set(), []
    for year in cfg["lobbying"]["filing_years"]:
        for q in variants(btype, number):
            url, params = API, {"filing_specific_lobbying_issues": q, "filing_year": year, "page_size": 25}
            while url:
                data = http.json(url, params=params, headers=headers)
                for f in data.get("results", []):
                    uid = f.get("filing_uuid")
                    if uid in seen:
                        continue
                    text = " ".join((a.get("description") or "") for a in f.get("lobbying_activities", []))
                    if not rx.search(text):
                        continue
                    seen.add(uid)
                    filings.append({
                        "filing_uuid": uid, "year": f.get("filing_year"), "period": f.get("filing_period"),
                        "posted": f.get("dt_posted"), "amount": amount(f),
                        "registrant": (f.get("registrant") or {}).get("name"),
                        "client": client_name((f.get("client") or {}).get("name")),
                        "url": f.get("filing_document_url") or f.get("url")})
                url, params = data.get("next"), None
    return filings


def pac_index(cmte):
    """normalized organization name -> [PAC committee IDs]"""
    idx = {}
    for cid, c in cmte.items():
        if c.get("CMTE_TP") not in PAC_TYPES or c.get("CMTE_DSGN") not in PAC_DESIGNATIONS:
            continue
        for nm in (c.get("CONNECTED_ORG_NM"), c.get("CMTE_NM")):
            nm = (nm or "").upper()
            for k in {norm_name(nm), norm_name(re.sub(
                    r"\b(POLITICAL ACTION COMMITTEE|PAC|FEDERAL|SEPARATE SEGREGATED FUND)\b", " ", nm))}:
                if k:
                    idx.setdefault(k, set()).add(cid)
    return {k: sorted(v) for k, v in idx.items()}


def overrides():
    p = ROOT / "overrides" / "pac_map.yaml"
    raw = (yaml.safe_load(open(p, encoding="utf-8")) or {}) if p.exists() else {}
    return {norm_name(k): v for k, v in raw.items()}


ACRONYMS = {"AFL-CIO", "AFSCME", "NCAA", "NFL", "NHL", "NBA", "MLB", "SEIU", "AARP", "IBM", "AT&T", "USA", "PAC",
            "LGBTQ", "NAACP", "ACLU", "UAW", "BP", "GE", "CVS", "UPS"}
SMALL = {"of", "and", "the", "for", "in", "on", "at", "to", "a"}
SUFFIX = re.compile(r",?\s+\b(L\.?L\.?C|INC|INCORPORATED|CORP|CORPORATION|CO|LTD|L\.?P|PLC|HOLDINGS)\.?$", re.I)


def display_name(client):
    names = getattr(display_name, "_ov", None)
    if names is None:
        p = ROOT / "overrides" / "group_names.yaml"
        raw = (yaml.safe_load(open(p, encoding="utf-8")) or {}) if p.exists() else {}
        names = display_name._ov = {norm_name(k): v for k, v in raw.items()}
    if norm_name(client) in names:
        return names[norm_name(client)]
    s = re.sub(r"\s*\((?:FORMERLY|FORMELY|F/?K/?A|D/?B/?A)[^)]*\)", "", client, flags=re.I).strip()
    while SUFFIX.search(s):
        s = SUFFIX.sub("", s).strip()
    if s.upper() != s:          # already mixed case: trust it
        return s
    out = []
    for i, w in enumerate(s.split()):
        if w.upper() in ACRONYMS or (len(w) <= 4 and w.isalpha() and not re.search(r"[AEIOUY]", w.upper())):
            out.append(w.upper())
        elif i and w.lower() in SMALL:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


def pacs_for(client, idx, ov):
    k = norm_name(client)
    if k in ov:
        v = ov[k]
        return [] if v in (None, "none", []) else list(v)
    return idx.get(k, [])


def summarize(filings, idx, ov, cmte, limit):
    order = {"first_quarter": 1, "second_quarter": 2, "mid_year": 2, "third_quarter": 3, "fourth_quarter": 4,
             "year_end": 4}
    clients = {}
    for f in filings:
        c = clients.setdefault(norm_name(f["client"]), {"client": f["client"], "reports": 0, "latest": None,
                                                        "key": None})
        c["reports"] += 1
        # Use the client's most recent quarter. If several reports name the bill that quarter (its own and
        # outside firms'), take the largest one: a group's own report already includes what it paid firms.
        key = (f["year"] or 0, order.get(f["period"], 0))
        if c["key"] is None or key > c["key"] or (key == c["key"] and (f["amount"] or 0) > (c["latest"]["amount"] or 0)):
            c["key"], c["latest"] = key, f
    rows = []
    for c in clients.values():
        ids = pacs_for(c["client"], idx, ov)
        rows.append({"client": c["client"], "display_name": display_name(c["client"]), "reports": c["reports"],
                     "latest_report_amount": c["latest"]["amount"], "latest_report": c["latest"]["filing_uuid"],
                     "latest_report_period": f'{c["latest"]["year"]} {c["latest"]["period"]}',
                     "pac_ids": ids, "pac_names": [cmte.get(i, {}).get("CMTE_NM") for i in ids]})
    rows.sort(key=lambda r: -(r["latest_report_amount"] or 0))
    shown = [r for r in rows if r["pac_ids"]][:limit]
    return rows, shown


def update(cfg, http, bills, cmte, refresh_days=7, first=()):
    """bills: iterable of (congress, type, number) for passage votes."""
    idx, ov = pac_index(cmte), overrides()
    limit = cfg["lobbying"]["groups_per_bill"]
    now, done = time.time(), 0
    first = [b for b in dict.fromkeys(first)]
    for congress, btype, number in first + [b for b in sorted(set(bills)) if b not in first]:
        path = out_path(cfg, "lobbying", f"{btype}{number}-{congress}.json")
        old = read_json(path)
        if old and now - old.get("fetched_ts", 0) < refresh_days * 86400:
            # Still fresh: no download, but re-apply the current rules and PAC matches to the saved reports.
            rows, shown = summarize(old.get("filings", []), idx, ov, cmte, limit)
            if rows != old.get("clients") or shown != old.get("groups_shown"):
                old["clients"], old["groups_shown"] = rows, shown
                write_json(path, old)
            continue
        try:
            filings = search_bill(http, cfg, btype, number)
        except Budget:
            log.warning("Lobbying: request budget used up, stopping")
            break
        except Exception as e:
            log.warning("Lobbying search failed for %s %s: %s", btype, number, e)
            continue
        rows, shown = summarize(filings, idx, ov, cmte, limit)
        write_json(path, {
            "bill": f"{btype}{number}-{congress}", "filing_years": cfg["lobbying"]["filing_years"],
            "fetched_ts": int(now), "report_count": len(filings), "clients": rows, "groups_shown": shown,
            "rule": f"Groups shown: up to {limit} groups that have a federal PAC, ranked by the lobbying amount on "
                    "their latest report naming the bill.",
            "filings": filings,
            "source": "Lobbying Disclosure Act reports, lda.gov; PACs from the FEC committee file"})
        done += 1
    log.info("Lobbying: %d bills updated", done)


def load(cfg, bid):
    return read_json(Path(cfg["output_dir"]) / "lobbying" / f"{bid}.json")
