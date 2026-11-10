"""Bills scheduled for floor action this week and next.

House: the Majority Leader's weekly floor schedule, published as XML at docs.house.gov.
Senate: the Senate does not publish a list of bills for the coming week. Its floor schedule page
(senate.gov) usually gives only the time the Senate convenes. So Senate items come from three
official, nonpartisan records, and are labeled "expected," not "scheduled":
  1. Senate roll calls already saved by this pipeline: a bill on which the Senate voted to end
     debate (cloture) in the last two weeks, with no final vote on it since.
  2. Congress.gov bill actions: a bill whose latest action in the last 10 days is a cloture motion
     filed, a motion to proceed, or the Senate taking up or considering the bill.
  3. Any bill numbers on the senate.gov floor schedule page, when it lists them.
"""
import logging
import re
import os
import time
from pathlib import Path
import xml.etree.ElementTree as ET
from datetime import date, timedelta

from .core import _BILL_RE, BILL_TYPES, Budget, bill_id, bill_label, find_bills, out_path, read_json, write_json

log = logging.getLogger("solon")

HOUSE_XML = "https://docs.house.gov/floor/Download.aspx?file=/billsthisweek/{d}/{d}.xml"
SENATE_PAGE = "https://www.senate.gov/legislative/schedule/floor_schedule.htm"
CONGRESS_API = "https://api.congress.gov/v3/bill"
# Congress.gov action wording that means a bill is on, or about to reach, the Senate floor.
SENATE_PENDING = re.compile(
    r"cloture motion .*presented in senate|cloture .*filed in senate|motion to proceed .*(?:made|presented|agreed to) in senate|"
    r"measure laid before senate|considered by senate|cloture .*invoked in senate", re.I)
SENATE_DONE = re.compile(r"passed senate|failed of passage in senate|cloture .*not invoked|returned to the calendar", re.I)
SENATE_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SolonWatch-data-pipeline/1.0; +https://solonwatch.com)"}


BILL_LABEL_TYPES = {"hr", "s", "hjres", "sjres"}


def house_weeks(today=None):
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    # The House names each weekly file after the week's first day (Sunday or Monday).
    return [monday - timedelta(days=1), monday, monday + timedelta(days=6), monday + timedelta(days=7)]


def parse_house_week(xml_bytes, congress):
    root = ET.fromstring(xml_bytes)
    items = []
    for fi in root.iter("floor-item"):
        legis = " ".join((fi.findtext("legis-num") or "").split())
        text = " ".join((fi.findtext("floor-text") or "").split())
        bills = find_bills(legis) or find_bills(text)
        if not bills:
            continue
        btype, num = bills[0]
        cat = fi.get("category") or ""
        items.append({"bill": {"type": btype, "number": num, "label": bill_label(btype, num),
                               "id": bill_id(congress, btype, num)},
                      "description": text[:400], "category": cat})
    return items


def senate_items(html_text, congress):
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html_text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    seen, items = set(), []
    for m in _BILL_RE.finditer(text):
        btype = BILL_TYPES.get(re.sub(r"[^A-Z]", "", m.group(1).upper()))
        if not btype or (btype, int(m.group(2))) in seen:
            continue
        num = int(m.group(2))
        seen.add((btype, num))
        a = text.rfind(". ", 0, m.start()) + 1
        b = text.find(". ", m.end())
        sentence = text[a: b + 1 if b != -1 else len(text)].strip()
        items.append({"bill": {"type": btype, "number": num, "label": bill_label(btype, num),
                               "id": bill_id(congress, btype, num)},
                      "description": sentence[:400], "category": "expected"})
    return items


def _item(congress, btype, num, description, basis):
    return {"bill": {"type": btype, "number": num, "label": bill_label(btype, num), "id": bill_id(congress, btype, num)},
            "description": description[:400], "category": "expected", "basis": basis}


def long_day(iso):
    d = date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}"


def senate_from_votes(index, congress, today=None, days=14):
    """Bills the Senate voted to end debate on recently, with no final vote on the bill since."""
    today = today or date.today()
    start = (today - timedelta(days=days)).isoformat()
    sen = sorted((v for v in index if v["chamber"] == "senate" and v.get("bill")), key=lambda v: (v["date"], v["number"]))
    last = {}
    for v in sen:
        last[v["bill"]["id"]] = v
    out = []
    for bid, v in last.items():
        if v["date"] < start or v.get("kind") != "cloture" or not re.search(r"agreed|invoked", v.get("result") or "", re.I):
            continue
        b, t = v["bill"], v["totals"]
        what = "the motion to proceed to the bill" if "proceed" in (v.get("question") or "").lower() else "the bill"
        out.append(_item(congress, b["type"], b["number"],
                         f"The Senate voted {t['yea']} to {t['nay']} on {long_day(v['date'])} to end debate on {what}. "
                         f"More votes on it are expected.", f"senate roll call {v['number']}"))
    return out


def senate_from_congress(http, key, congress, today=None, days=10, pages=4):
    """Bills whose latest Congress.gov action shows them headed to or on the Senate floor."""
    today = today or date.today()
    start = today - timedelta(days=days)
    params = {"format": "json", "api_key": key, "limit": 250, "sort": "updateDate desc",
              "fromDateTime": start.strftime("%Y-%m-%dT00:00:00Z"), "toDateTime": today.strftime("%Y-%m-%dT23:59:59Z")}
    url, out, seen = f"{CONGRESS_API}/{congress}", [], set()
    for _ in range(pages):
        data = http.json(url, params=params, allow_404=True) or {}
        for b in data.get("bills", []):
            act = b.get("latestAction") or {}
            text, when = act.get("text") or "", act.get("actionDate") or ""
            btype = (b.get("type") or "").lower()
            if btype not in BILL_LABEL_TYPES or when < start.isoformat():
                continue
            if not SENATE_PENDING.search(text) or SENATE_DONE.search(text):
                continue
            num = int(b["number"])
            if (btype, num) in seen:
                continue
            seen.add((btype, num))
            title = " ".join((b.get("title") or "").split())
            out.append(_item(congress, btype, num, f"{title}. Latest action, {long_day(when)}: {text}", "congress.gov action"))
        nxt = (data.get("pagination") or {}).get("next")
        if not nxt:
            break
        url, params = nxt, {"api_key": key, "format": "json"}
    return out


def merge_senate(*lists):
    seen, out = set(), []
    for lst in lists:
        for it in lst:
            if it["bill"]["id"] in seen:
                continue
            seen.add(it["bill"]["id"])
            out.append(it)
    return out


def update(cfg, http):
    congress = cfg["congress"]
    house, used = [], None
    for d in house_weeks():
        ds = d.strftime("%Y%m%d")
        r = http.get(HOUSE_XML.format(d=ds), allow_404=True)
        if r is None or b"<floor-item" not in r.content:
            continue
        try:
            items = parse_house_week(r.content, congress)
        except ET.ParseError:
            continue
        for it in items:
            it["week_of"] = d.isoformat()
        house.extend(items)
        used = used or ds
    index = (read_json(Path(cfg["output_dir"]) / "votes" / "index.json") or {}).get("votes", [])
    from_votes = senate_from_votes(index, congress)
    from_api, from_page = [], []
    key = os.environ.get("API_DATA_GOV_KEY")
    if key:
        try:
            from_api = senate_from_congress(http, key, congress)
        except Budget:
            log.warning("Upcoming: Congress.gov request budget used up")
        except Exception as e:
            log.warning("Senate actions from Congress.gov not read: %s", e)
    try:
        r = http.get(SENATE_PAGE, headers=SENATE_HEADERS, allow_404=True)
        if r is not None:
            from_page = senate_items(r.text, congress)
    except Exception as e:
        log.warning("Senate floor schedule not read: %s", e)
    senate = merge_senate(from_votes, from_api, from_page)
    log.info("Upcoming Senate: %d from roll calls, %d from Congress.gov, %d from senate.gov",
             len(from_votes), len(from_api), len(from_page))
    out = {
        "updated": time.strftime("%Y-%m-%d"),
        "house": house, "senate": senate,
        "sources": {"house": "House Majority Leader floor schedule, docs.house.gov",
                    "senate": "Expected, not scheduled: Senate roll calls (senate.gov), bill actions (Congress.gov) "
                              "and the senate.gov floor schedule"},
    }
    write_json(out_path(cfg, "upcoming.json"), out)
    log.info("Upcoming: %d House items, %d Senate items", len(house), len(senate))
    return [(congress, i["bill"]["type"], i["bill"]["number"]) for i in house + senate]
