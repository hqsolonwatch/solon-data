"""Bills scheduled for floor action this week and next.

House: the Majority Leader's weekly floor schedule, published as XML at docs.house.gov.
Senate: the Senate floor schedule page on senate.gov. The Senate posts its plans as text,
so bill numbers are picked out of the wording. Treat Senate items as "expected," not certain.
"""
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta

from .core import _BILL_RE, BILL_TYPES, bill_id, bill_label, find_bills, out_path, write_json

log = logging.getLogger("solon")

HOUSE_XML = "https://docs.house.gov/floor/Download.aspx?file=/billsthisweek/{d}/{d}.xml"
SENATE_PAGE = "https://www.senate.gov/legislative/schedule/floor_schedule.htm"
SENATE_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SolonWatch-data-pipeline/1.0; +https://solonwatch.com)"}


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
    senate = []
    try:
        r = http.get(SENATE_PAGE, headers=SENATE_HEADERS, allow_404=True)
        if r is not None:
            senate = senate_items(r.text, congress)
    except Exception as e:
        log.warning("Senate floor schedule not read: %s", e)
    out = {
        "updated": time.strftime("%Y-%m-%d"),
        "house": house, "senate": senate,
        "sources": {"house": "House Majority Leader floor schedule, docs.house.gov",
                    "senate": "Senate floor schedule, senate.gov (bill numbers read from the posted text)"},
    }
    write_json(out_path(cfg, "upcoming.json"), out)
    log.info("Upcoming: %d House items, %d Senate items", len(house), len(senate))
    return [(congress, i["bill"]["type"], i["bill"]["number"]) for i in house + senate]
