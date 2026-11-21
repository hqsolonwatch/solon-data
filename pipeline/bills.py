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


HEADING = re.compile(r"<(?:strong|b)>\s*((?:Division|Title|Subtitle)\s+[A-Z0-9]+\s*[\u2014\u2013:-]+\s*[^<]{3,120}?)\s*</(?:strong|b)>", re.I)


def key_points(summary_html, limit=6):
    """The main parts of a long bill, from the Division and Title headings in the CRS summary."""
    out = []
    for m in HEADING.finditer(summary_html or ""):
        h = strip_html(m.group(1))
        h = re.sub(r"^(Division|Title|Subtitle)\s+[A-Z0-9]+\s*[\u2014\u2013:-]+\s*", "", h, flags=re.I).strip(" .")
        if h and h.lower() not in (x.lower() for x in out):
            out.append(h[:1].upper() + h[1:])
    return out[:limit]


def override(bid):
    """A hand-written entry from overrides/summaries.yaml: either one sentence, or a mapping with
    summary (one sentence), points (the other important things in the bill), title and source."""
    o = overrides().get(bid)
    if isinstance(o, str):
        return {"summary": o}
    return o or {}


def short_summary(b):
    """The one-sentence summary to show for a saved bill file (hand-written override first).
    A summary written for text that was later replaced is not shown."""
    o = override(b.get("id"))
    if o.get("summary"):
        return o["summary"]
    if b.get("summary_outdated"):
        return None
    return first_sentence(b["summary"]) if b.get("summary") else None


def points(b):
    """Other important things in the bill, beyond its headline sentence."""
    o = override(b.get("id"))
    if o.get("points"):
        return o["points"]
    if b.get("summary_outdated"):
        return []
    return (b.get("key_points") or []) + [f"Includes the {t}" for t in (b.get("included_acts") or [])][:6]


def current_title(b):
    o = override(b.get("id"))
    return o.get("title") or b.get("current_title") or b.get("short_title") or b.get("title")


SUBSTITUTE = re.compile(r"in the nature of a substitute", re.I)
AGREED = re.compile(r"agreed to", re.I)
AMDT_LABEL = {"SAMDT": "S.Amdt.", "HAMDT": "H.Amdt.", "SUAMDT": "S.Up.Amdt."}


def text_replacement(amendments):
    """The latest adopted amendment that replaced the bill's whole text, if any.
    The Senate often passes a House bill number carrying entirely different text."""
    hits = []
    for a in amendments or []:
        what = " ".join(filter(None, [a.get("purpose"), a.get("description")]))
        act = a.get("latestAction") or {}
        if SUBSTITUTE.search(what) and AGREED.search(act.get("text") or ""):
            hits.append({"label": f'{AMDT_LABEL.get((a.get("type") or "").upper(), "Amdt.")} {a.get("number")}',
                         "date": act.get("actionDate"), "action": act.get("text"),
                         "chamber": "Senate" if (a.get("type") or "").upper().startswith("S") else "House"})
    return max(hits, key=lambda h: h["date"] or "") if hits else None


def pick_titles(titles):
    """The bill's short title for its latest version, and the acts it carries 'for portions of this bill'."""
    shorts = [t for t in titles or [] if "short title" in (t.get("titleType") or "").lower()]
    whole = [t for t in shorts if "portions" not in t["titleType"].lower()]
    parts = [t for t in shorts if "portions" in t["titleType"].lower()]
    key = lambda t: (t.get("updateDate") or "", t.get("billTextVersionCode") or "")
    current = max(whole, key=key)["title"] if whole else None
    if parts:
        newest = max(key(t) for t in parts)[0][:10]
        included = list(dict.fromkeys(t["title"] for t in parts if (t.get("updateDate") or "")[:10] == newest))
    else:
        included = []
    return current, included


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
    amdts = (http.json(base + "/amendments", params={**p, "limit": 250}, allow_404=True) or {}).get("amendments", [])
    sums.sort(key=lambda s: (s.get("actionDate") or "", s.get("updateDate") or ""))
    latest = sums[-1] if sums else None
    text = strip_html(latest["text"]) if latest else ""
    short = next((t["title"] for t in titles if "short title" in (t.get("titleType") or "").lower()), None)
    current, included = pick_titles(titles)
    replaced = text_replacement(amdts)
    # A CRS summary dated before the text was replaced describes text that is no longer in the bill.
    outdated = bool(replaced and latest and (latest.get("actionDate") or "") < (replaced["date"] or ""))
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
        "key_points": key_points(latest["text"]) if latest else [],
        "current_title": current, "included_acts": included,
        "text_replaced": replaced, "summary_outdated": outdated,
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
        o = override(b["id"])
        if o.get("summary"):
            b["summary_short"] = o["summary"]
            b["summary_source"] = o.get("source") or "Solon Watch, based on the Congressional Research Service summary"
        write_json(path, b)
        done += 1
    log.info("Bills: %d updated", done)


def load(cfg, bid):
    return read_json(Path(cfg["output_dir"]) / "bills" / f"{bid}.json")
