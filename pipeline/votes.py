"""Roll-call votes from the official House Clerk and Senate records.

House:  https://clerk.house.gov/evs/{year}/roll{NNN}.xml
Senate: https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{congress}_{session}.xml
        https://www.senate.gov/legislative/LIS/roll_call_votes/vote{congress}{session}/vote_{congress}_{session}_{NNNNN}.xml
"""
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from .core import Budget, bill_id, bill_label, out_path, parse_bill, read_json, write_json

log = logging.getLogger("solon")

# senate.gov turns away some automated clients, so identify as a normal browser too.
SENATE_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SolonWatch-data-pipeline/1.0; +https://solonwatch.com)"}

POSITION = {"yea": "Yea", "aye": "Yea", "nay": "Nay", "no": "Nay", "present": "Present",
            "not voting": "Not Voting", "guilty": "Yea", "not guilty": "Nay"}


def classify(question, chamber, bill):
    q = (question or "").lower()
    if "cloture" in q:
        return "cloture"
    if "nomination" in q or "treaty" in q:
        return "nomination"
    if "amendment" in q and "concur" not in q:
        return "amendment"
    if any(k in q for k in ("previous question", "motion to table", "motion to recommit", "motion to commit",
                            "motion to proceed", "quorum", "journal", "adjourn", "motion to discharge",
                            "point of order", "motion to refer", "motion to instruct", "waive", "appeal")):
        return "procedural"
    if bill is None:
        return "other"
    passage_words = ("on passage", "suspend the rules and pass", "suspend the rules and agree",
                     "on the joint resolution", "on the resolution", "on the concurrent resolution",
                     "on agreeing to the resolution", "on the bill")
    if any(k in q for k in passage_words):
        # House rules ("H.Res." on agreeing to a rule) are procedural, not passage of legislation.
        if bill[0] == "hres" and "on agreeing to the resolution" in q and chamber == "house":
            return "resolution"
        return "passage"
    if "conference report" in q or "concur" in q or "override" in q or "veto" in q:
        return "final_action"
    return "other"


def _t(el, path, default=""):
    x = el.find(path)
    return (x.text or "").strip() if x is not None and x.text else default


# ---------------- House ----------------

def parse_house(xml_text):
    root = ET.fromstring(xml_text)
    md = root.find("vote-metadata")
    congress = int(_t(md, "congress"))
    session = int(re.sub(r"\D", "", _t(md, "session")) or 0)
    num = int(_t(md, "rollcall-num"))
    legis = _t(md, "legis-num")
    bill = parse_bill(legis)
    question = _t(md, "vote-question")
    date = datetime.strptime(_t(md, "action-date"), "%d-%b-%Y").date().isoformat()
    by_party = {}
    tot = md.find("vote-totals")
    if tot is not None:
        for p in tot.findall("totals-by-party"):
            party = _t(p, "party")
            by_party[party] = {k: int(_t(p, f"{k}-total", "0") or 0) for k in ("yea", "nay", "present", "not-voting")}
        tv = tot.find("totals-by-vote")
        totals = {k: int(_t(tv, f"{k}-total", "0") or 0) for k in ("yea", "nay", "present", "not-voting")} if tv is not None else {}
    else:
        totals = {}
    positions = []
    for rv in root.iter("recorded-vote"):
        leg = rv.find("legislator")
        positions.append({"id": leg.get("name-id"), "party": leg.get("party"), "state": leg.get("state"),
                          "vote": POSITION.get(_t(rv, "vote").lower(), _t(rv, "vote"))})
    return {
        "chamber": "house", "congress": congress, "session": session, "number": num,
        "id": f"house-{congress}-{session}-{num}",
        "date": date, "question": question, "vote_type": _t(md, "vote-type"),
        "result": _t(md, "vote-result"), "description": _t(md, "vote-desc"),
        "bill": None if not bill else {"type": bill[0], "number": bill[1], "label": bill_label(*bill),
                                       "id": bill_id(congress, *bill)},
        "kind": classify(question, "house", bill),
        "totals": {"yea": totals.get("yea"), "nay": totals.get("nay"), "present": totals.get("present"),
                   "not_voting": totals.get("not-voting")},
        "by_party": by_party,
        "positions": positions,
        "source_url": None,
    }


def update_house(cfg, http, year):
    state_file = out_path(cfg, "state", f"house_{year}.json")
    state = read_json(state_file, {"last": 0})
    n = state["last"] + 1
    misses, new = 0, 0
    while misses < 3:
        url = f"https://clerk.house.gov/evs/{year}/roll{n:03d}.xml"
        r = http.get(url, allow_404=True)
        if r is None or b"<rollcall-vote" not in r.content:
            misses += 1
            n += 1
            continue
        misses = 0
        v = parse_house(r.content)
        v["source_url"] = url
        save_vote(cfg, v)
        state["last"] = n
        new += 1
        n += 1
    write_json(state_file, state)
    log.info("House %d: %d new roll calls (last %d)", year, new, state["last"])
    return new


# ---------------- Senate ----------------

def parse_senate(xml_text, lis_to_bioguide):
    root = ET.fromstring(xml_text)
    congress = int(_t(root, "congress"))
    session = int(_t(root, "session"))
    num = int(_t(root, "vote_number"))
    date_text = _t(root, "vote_date")
    try:
        date = datetime.strptime(date_text, "%B %d, %Y, %I:%M %p").date().isoformat()
    except ValueError:
        date = datetime.strptime(date_text.split(",")[0] + "," + date_text.split(",")[1], "%B %d, %Y").date().isoformat()
    doc = root.find("document")
    doc_name = " ".join(filter(None, [_t(doc, "document_type"), _t(doc, "document_number")])) if doc is not None else ""
    bill = parse_bill(doc_name) or parse_bill(_t(root, "vote_document_text"))
    question = _t(root, "question") or _t(root, "vote_question_text")
    c = root.find("count")
    totals = {"yea": int(_t(c, "yeas", "0") or 0), "nay": int(_t(c, "nays", "0") or 0),
              "present": int(_t(c, "present", "0") or 0), "not_voting": int(_t(c, "absent", "0") or 0)}
    positions, by_party = [], {}
    for m in root.iter("member"):
        party = _t(m, "party")
        vote = POSITION.get(_t(m, "vote_cast").lower(), _t(m, "vote_cast"))
        lis = _t(m, "lis_member_id")
        positions.append({"id": lis_to_bioguide.get(lis, lis), "lis": lis, "party": party,
                          "state": _t(m, "state"), "vote": vote})
        bp = by_party.setdefault(party, {"yea": 0, "nay": 0, "present": 0, "not-voting": 0})
        key = {"Yea": "yea", "Nay": "nay", "Present": "present"}.get(vote, "not-voting")
        bp[key] += 1
    return {
        "chamber": "senate", "congress": congress, "session": session, "number": num,
        "id": f"senate-{congress}-{session}-{num}",
        "date": date, "question": question, "vote_type": _t(root, "majority_requirement"),
        "result": _t(root, "vote_result_text") or _t(root, "vote_result"),
        "description": _t(root, "vote_title"),
        "bill": None if not bill else {"type": bill[0], "number": bill[1], "label": bill_label(*bill),
                                       "id": bill_id(congress, *bill)},
        "kind": classify(question, "senate", bill),
        "totals": totals, "by_party": by_party, "positions": positions, "source_url": None,
    }


def update_senate(cfg, http, congress, session, lis_to_bioguide):
    menu_url = f"https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{congress}_{session}.xml"
    r = http.get(menu_url, headers=SENATE_HEADERS, allow_404=True)
    if r is None:
        log.info("Senate %d-%d: no vote list yet", congress, session)
        return 0
    menu = ET.fromstring(r.content)
    nums = sorted({int(_t(v, "vote_number")) for v in menu.iter("vote") if _t(v, "vote_number")})
    have = {int(p.stem.split("-")[-1]) for p in Path(out_path(cfg, "votes", "senate", "x").parent).glob(f"{congress}-{session}-*.json")}
    new = 0
    for n in nums:
        if n in have:
            continue
        url = (f"https://www.senate.gov/legislative/LIS/roll_call_votes/vote{congress}{session}/"
               f"vote_{congress}_{session}_{n:05d}.xml")
        try:
            r = http.get(url, headers=SENATE_HEADERS, allow_404=True)
        except Budget:
            break
        if r is None:
            continue
        v = parse_senate(r.content, lis_to_bioguide)
        v["source_url"] = url
        save_vote(cfg, v)
        new += 1
    log.info("Senate %d-%d: %d new roll calls (%d listed)", congress, session, new, len(nums))
    return new


# ---------------- Storage and indexes ----------------

def save_vote(cfg, v):
    write_json(out_path(cfg, "votes", v["chamber"], f'{v["congress"]}-{v["session"]}-{v["number"]}.json'), v)


def all_votes(cfg):
    base = Path(cfg["output_dir"]) / "votes"
    for chamber in ("house", "senate"):
        for p in sorted((base / chamber).glob("*.json")):
            v = read_json(p)
            if v:
                yield v


def build_indexes(cfg, members):
    """votes/index.json (every vote, no member positions) and members/{id}/votes.json."""
    index, per_member = [], {}
    for v in all_votes(cfg):
        summary = {k: v[k] for k in ("id", "chamber", "congress", "session", "number", "date", "question",
                                     "kind", "result", "bill", "totals", "by_party", "source_url")}
        index.append(summary)
        for pos in v["positions"]:
            per_member.setdefault(pos["id"], []).append({
                "vote_id": v["id"], "date": v["date"], "vote": pos["vote"], "kind": v["kind"],
                "question": v["question"], "result": v["result"], "bill": v["bill"],
                "totals": v["totals"], "source_url": v["source_url"]})
    index.sort(key=lambda s: (s["date"], s["chamber"], s["number"]), reverse=True)
    write_json(out_path(cfg, "votes", "index.json"), {"count": len(index), "votes": index})
    known = {m["bioguide"] for m in members}
    for mid, rows in per_member.items():
        if mid not in known:
            continue
        rows.sort(key=lambda r: (r["date"], r["vote_id"]), reverse=True)
        latest_passage = next((r for r in rows if r["kind"] == "passage" and r["vote"] != "Not Voting"), None)
        write_json(out_path(cfg, "members", mid, "votes.json"),
                   {"bioguide": mid, "latest_passage_vote": latest_passage, "votes": rows})
    log.info("Vote index: %d votes, %d members with votes", len(index), len(per_member))
    return index


def update_all(cfg, http, members):
    lis = {m["lis"]: m["bioguide"] for m in members if m.get("lis")}
    congress = cfg["congress"]
    first_year = 1789 + 2 * (congress - 1)
    for year in cfg.get("house_years", []):
        try:
            update_house(cfg, http, year)
        except Exception as e:
            log.error("House votes for %d not updated: %s", year, e)
    for year in cfg.get("house_years", []):
        session = year - first_year + 1
        if session in (1, 2):
            try:
                update_senate(cfg, http, congress, session, lis)
            except Exception as e:
                log.error("Senate votes for session %d not updated: %s", session, e)
    return build_indexes(cfg, members)
