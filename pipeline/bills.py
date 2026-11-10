"""Bill details and plain-English summaries from the Congress.gov API.

Summaries are written by the Library of Congress's Congressional Research Service (CRS),
which is nonpartisan. A hand-written summary in overrides/summaries.yaml replaces the
automatic one for any bill listed there.
"""
import html
import logging
import os
import re
import time
from pathlib import Path

import yaml

from .core import ROOT, Budget, bill_id, bill_label, out_path, read_json, write_json

log = logging.getLogger("solon")
API = "https://api.congress.gov/v3"


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


BOILERPLATE = re.compile(r"^The following analysis is based on draft bill text.*?as introduced\.\s*", re.I | re.S)
OPENING = re.compile(r"\bThis (bill|act|joint resolution|concurrent resolution|resolution|measure) ", re.I)


def first_sentence(text, limit=320):
    """One plain sentence from a CRS summary: skips the draft-text notice and the repeated bill title."""
    text = BOILERPLATE.sub("", text or "").strip()
    m = OPENING.search(text[:400])
    if m:
        text = text[m.end():]
        text = text[:1].upper() + text[1:]
    m = re.search(r"^(.{40,}?[.;])(\s|$)", text)
    s = m.group(1) if m else text
    if s.endswith(";"):
        s = s[:-1] + "."
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0] + "…"
    return s


def short_summary(b):
    """The one-sentence summary to show for a saved bill file (hand-written override first)."""
    o = overrides().get(b.get("id"))
    if o:
        return o
    return first_sentence(b["summary"]) if b.get("summary") else None


def overrides():
    p = ROOT / "overrides" / "summaries.yaml"
    return (yaml.safe_load(open(p, encoding="utf-8")) or {}) if p.exists() else {}


URL_TYPE = {"hr": "house-bill", "s": "senate-bill", "hjres": "house-joint-resolution",
            "sjres": "senate-joint-resolution", "hres": "house-resolution", "sres": "senate-resolution",
            "hconres": "house-concurrent-resolution", "sconres": "senate-concurrent-resolution"}


def ordinal(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def congress_url(congress, btype, number):
    return f"https://www.congress.gov/bill/{ordinal(congress)}-congress/{URL_TYPE[btype]}/{number}"


def fetch_bill(http, key, congress, btype, number):
    base = f"{API}/bill/{congress}/{btype}/{number}"
    p = {"format": "json", "api_key": key}
    b = (http.json(base, params=p) or {}).get("bill", {})
    sums = (http.json(base + "/summaries", params=p, allow_404=True) or {}).get("summaries", [])
    titles = (http.json(base + "/titles", params=p, allow_404=True) or {}).get("titles", [])
    sums.sort(key=lambda s: (s.get("actionDate") or "", s.get("updateDate") or ""))
    latest = sums[-1] if sums else None
    text = strip_html(latest["text"]) if latest else ""
    short = next((t["title"] for t in titles if "short title" in (t.get("titleType") or "").lower()), None)
    sponsor = (b.get("sponsors") or [{}])[0]
    return {
        "id": bill_id(congress, btype, number), "label": bill_label(btype, number),
        "congress": congress, "type": btype, "number": number,
        "title": b.get("title"), "short_title": short,
        "introduced": b.get("introducedDate"),
        "sponsor": {"name": sponsor.get("fullName"), "bioguide": sponsor.get("bioguideId"),
                    "party": sponsor.get("party"), "state": sponsor.get("state")} if sponsor else None,
        "policy_area": (b.get("policyArea") or {}).get("name"),
        "latest_action": b.get("latestAction"),
        "became_law": bool(b.get("laws")),
        "summary": text or None,
        "summary_short": first_sentence(text) if text else None,
        "summary_version": latest.get("actionDesc") if latest else None,
        "summary_source": "Congressional Research Service, via Congress.gov" if text else None,
        "congress_url": congress_url(congress, btype, number),
        "fetched": time.strftime("%Y-%m-%d"),
    }


def update(cfg, http, wanted, refresh_days=3, first=()):
    """wanted: iterable of (congress, type, number)."""
    key = os.environ.get("API_DATA_GOV_KEY")
    if not key:
        log.warning("Bills: no API_DATA_GOV_KEY set, skipping Congress.gov")
        return
    ov = overrides()
    today = time.time()
    done = 0
    first = [b for b in dict.fromkeys(first)]
    for congress, btype, number in first + [b for b in sorted(set(wanted)) if b not in first]:
        path = out_path(cfg, "bills", f"{bill_id(congress, btype, number)}.json")
        old = read_json(path)
        if old:
            # Bills still moving are refreshed every few days; settled ones about once a month.
            last = ((old.get("latest_action") or {}).get("actionDate") or "")
            recent = last >= time.strftime("%Y-%m-%d", time.gmtime(today - 45 * 86400))
            active = (not old.get("summary")) or (recent and not old.get("became_law"))
            # Age comes from the date saved in the file. File times are reset on every GitHub checkout.
            if old.get("fetched", "") > time.strftime("%Y-%m-%d", time.gmtime(today - (refresh_days if active else 30) * 86400)):
                continue
        try:
            b = fetch_bill(http, key, congress, btype, number)
        except Budget:
            log.warning("Bills: request budget used up, stopping")
            break
        except Exception as e:  # keep going if one bill fails
            log.warning("Bills: %s %s failed: %s", btype, number, e)
            continue
        o = ov.get(b["id"])
        if o:
            b["summary_short"] = o
            b["summary_source"] = "Solon Watch, based on the Congressional Research Service summary"
        write_json(path, b)
        done += 1
    log.info("Bills: %d updated", done)


def load(cfg, bid):
    return read_json(Path(cfg["output_dir"]) / "bills" / f"{bid}.json")
