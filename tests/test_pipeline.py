"""Offline tests. No internet needed: web requests are answered from tests/fixtures or built here.

Run:  pytest -q
Set SOLON_TEST_FEC_DIR to a folder with real weball26.txt and itpas2.txt, and SOLON_TEST_CENSUS_DIR to a
folder with cb_2025_us_cd119_500k.zip and cb_2025_us_county_500k.zip, to also test with real files.
"""
import json
import os
import re
from pathlib import Path

import pytest

from pipeline import bills, core, fec, landing, lobbying, members, upcoming, votes
from pipeline.core import load_config

FIX = Path(__file__).parent / "fixtures"


class Resp:
    def __init__(self, body, status=200):
        self.content = body if isinstance(body, bytes) else body.encode()
        self.text = self.content.decode("utf-8", "replace")
        self.status_code = status

    def json(self):
        return json.loads(self.text)


class FakeHttp:
    """Answers requests from a list of (regex, response) rules."""

    def __init__(self, rules):
        self.rules = rules
        self.counts = {}
        self.calls = []

    def get(self, url, params=None, headers=None, allow_404=False, timeout=60, retries=4):
        full = url + ("?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items())) if params else "")
        self.calls.append(full)
        for pat, body in self.rules:
            if re.search(pat, full):
                body = body(full) if callable(body) else body
                if body is None:
                    break
                return Resp(body)
        if allow_404:
            return None
        raise AssertionError("Unexpected request: " + full)

    def json(self, url, **kw):
        r = self.get(url, **kw)
        return None if r is None else r.json()


LEGISLATORS = [
    {"id": {"bioguide": "O000174", "lis": "S414", "fec": ["S8GA00180", "H8GA06195"]},
     "name": {"first": "Jon", "last": "Ossoff", "official_full": "Jon Ossoff"},
     "terms": [{"type": "sen", "state": "GA", "party": "Democrat", "class": 2, "start": "2021-01-20",
                "end": "2027-01-03", "url": "https://www.ossoff.senate.gov", "phone": "202-224-3521",
                "address": "317 Hart Senate Office Building Washington DC 20510",
                "contact_form": "https://www.ossoff.senate.gov/contact"}]},
    {"id": {"bioguide": "C001035", "lis": "S252", "fec": ["S6ME00159"]},
     "name": {"first": "Susan", "last": "Collins", "official_full": "Susan M. Collins"},
     "terms": [{"type": "sen", "state": "ME", "party": "Republican", "class": 2, "start": "2021-01-03",
                "end": "2027-01-03", "url": "https://www.collins.senate.gov", "phone": "202-224-2523"}]},
    {"id": {"bioguide": "C001101", "fec": ["H4MA05084"]},
     "name": {"first": "Katherine", "last": "Clark", "official_full": "Katherine M. Clark"},
     "terms": [{"type": "rep", "state": "MA", "district": 5, "party": "Democrat", "start": "2025-01-03",
                "end": "2027-01-03", "url": "https://katherineclark.house.gov", "phone": "202-225-2836"}]},
    {"id": {"bioguide": "E000294", "fec": ["H4MN06087"]},
     "name": {"first": "Tom", "last": "Emmer", "official_full": "Tom Emmer"},
     "terms": [{"type": "rep", "state": "MN", "district": 6, "party": "Republican", "start": "2025-01-03",
                "end": "2027-01-03", "url": "https://emmer.house.gov", "phone": "202-225-2331"}]},
    {"id": {"bioguide": "S001176", "fec": ["H8LA01087"]},
     "name": {"first": "Steve", "last": "Scalise", "official_full": "Steve Scalise"},
     "terms": [{"type": "rep", "state": "LA", "district": 1, "party": "Republican", "start": "2025-01-03",
                "end": "2027-01-03"}]},
    {"id": {"bioguide": "K000375", "fec": ["H0MA10082"]},
     "name": {"first": "William", "last": "Keating", "official_full": "William R. Keating"},
     "terms": [{"type": "rep", "state": "MA", "district": 9, "party": "Democrat", "start": "2025-01-03",
                "end": "2027-01-03"}]},
]

HOUSE_FLOOR = b"""<?xml version="1.0"?><floorschedule week-date="2026-11-16">
<category type="suspension"><floor-items>
<floor-item id="1" add-date="2026-11-13"><legis-num>H.R. 7008</legis-num><floor-text>Stop Trading on Congressional Knowledge Update Act</floor-text></floor-item>
</floor-items></category></floorschedule>"""
SENATE_FLOOR = "<html><body><p>The Senate will resume consideration of H.R. 9340, the Ratepayer Protection Act.</p><p>No votes before 5:30 p.m.; see U.S. 30 days.</p></body></html>"


def bill_json(label, title, short, summary):
    return {
        "bill": json.dumps({"bill": {"title": title, "introducedDate": "2026-06-02", "laws": [],
                                     "sponsors": [{"fullName": "Sen. Example", "bioguideId": "X0001", "party": "R", "state": "TX"}],
                                     "latestAction": {"actionDate": "2026-09-28", "text": "Passed Senate."},
                                     "policyArea": {"name": "Sports and Recreation"}}}),
        "summaries": json.dumps({"summaries": [{"actionDate": "2026-06-24", "actionDesc": "Reported to Senate",
                                                "updateDate": "2026-09-21", "text": f"<p>{summary}</p>"}]}),
        "titles": json.dumps({"titles": [{"title": short, "titleType": "Short Title(s) as Introduced"},
                                         {"title": title, "titleType": "Official Title as Introduced"}]}),
    }


B4668 = bill_json("S. 4668", "A bill to protect the name, image, and likeness rights of student athletes.",
                  "Protect College Sports Act of 2026",
                  "This bill establishes requirements for name, image, or likeness (NIL) agreements for college "
                  "student athletes. It also creates a limited antitrust exemption.")
B2403 = bill_json("S. 2403", "A bill to amend ERISA.", "Retire through Ownership Act",
                  "This bill allows the fiduciary of an Employee Stock Ownership Plan (ESOP) to rely on a valuation. More.")

CM_ROWS = [
    ["C00360354", "AMAZON.COM SERVICES LLC SEPARATE SEGREGATED FUND (AMAZON PAC)", "", "", "", "", "VA", "", "B", "Q", "", "M", "C", "AMAZON.COM SERVICES LLC", ""],
    ["C00009985", "NATIONAL ASSOCIATION OF BROADCASTERS POLITICAL ACTION COMMITTEE (NABPAC)", "", "", "", "", "DC", "", "B", "Q", "", "M", "T", "NATIONAL ASSOCIATION OF BROADCASTERS", ""],
    ["C00011114", "AFSCME P.E.O.P.L.E.", "", "", "", "", "DC", "", "B", "Q", "", "M", "L", "AMERICAN FEDERATION OF STATE COUNTY AND MUNICIPAL EMPLOYEES", ""],
    ["C00104299", "JPMORGAN CHASE & CO. FEDERAL POLITICAL ACTION COMMITTEE", "", "", "", "", "NY", "", "B", "Q", "", "M", "C", "JPMORGAN CHASE & CO.", ""],
    ["C00128918", "PRINCIPAL LIFE INSURANCE COMPANY POLITICAL ACTION COMMITTEE", "", "", "", "", "IA", "", "B", "Q", "", "M", "C", "PRINCIPAL LIFE INSURANCE COMPANY", ""],
]


def lda_result(uuid, client, desc, amount, posted):
    return {"filing_uuid": uuid, "filing_year": 2026, "filing_period": "third_quarter", "dt_posted": posted,
            "income": None, "expenses": str(amount), "registrant": {"name": client}, "client": {"name": client},
            "lobbying_activities": [{"general_issue_code": "X", "description": desc}]}


def lda_rules():
    s4668 = {"results": [
        lda_result("a1", "AMAZON.COM SERVICES LLC", "S. 4668, Protect College Sports Act", 4490000, "2026-10-20"),
        lda_result("a2", "NATIONAL ASSOCIATION OF BROADCASTERS", "S. 4668", 2700000, "2026-10-20"),
        lda_result("a3", "DUKE UNIVERSITY", "S. 4668, Protect College Sports Act of 2026.", 80000, "2026-10-20"),
        lda_result("a4", "AMERICAN FEDERATION OF STATE COUNTY AND MUNICIPAL EMPLOYEES", "Protect College Sports Act (S. 4668)", 570000, "2026-10-20"),
        lda_result("a5", "SOMEONE ELSE", "S. 46680 is a different number", 999999999, "2026-10-20"),
    ], "next": None}
    s2403 = {"results": [
        lda_result("b1", "PRINCIPAL FINANCIAL GROUP", "S2403 Retire Through Ownership Act.", 300000, "2026-10-19"),
        lda_result("b2", "JPMorgan Chase Holdings LLC", "S. 2403 - Discussed succession planning issues.", 3000000, "2026-10-19"),
        lda_result("b3", "AMERICAN BENEFITS COUNCIL", "Retire Through Ownership Act, HR 5160 / S. 2403", 500000, "2026-04-15"),
    ], "next": None}

    def answer(url):
        if "2403" in url and "filing_year=2026" in url:
            return json.dumps(s2403)
        if "4668" in url and "filing_year=2026" in url:
            return json.dumps(s4668)
        return json.dumps({"results": [], "next": None})
    return [(r"lda\.gov/api/v1/filings", answer)]


def fec_api_rules():
    cid = {"S8GA00180": "C00718866", "S6ME00159": "C00314575", "H4MA05084": "C00541888", "H4MN06087": "C00545749"}
    totals = {"C00718866": dict(receipts=115904485.77, individual_itemized_contributions=46885098.11,
                                individual_unitemized_contributions=55343417.20, other_political_committee_contributions=1088328.60,
                                political_party_committee_contributions=11000, candidate_contribution=0, loans_made_by_candidate=0,
                                transfers_from_other_authorized_committee=11622454.75, other_receipts=946886.33,
                                coverage_start_date="2025-01-01T00:00:00", coverage_end_date="2026-10-14T00:00:00"),
              "C00314575": dict(receipts=22465217.83, individual_itemized_contributions=12660729.00,
                                individual_unitemized_contributions=2639249.61, other_political_committee_contributions=1593453.75,
                                political_party_committee_contributions=62000, candidate_contribution=0, loans_made_by_candidate=0,
                                transfers_from_other_authorized_committee=5371811.34, other_receipts=137974.13,
                                coverage_start_date="2025-01-01T00:00:00", coverage_end_date="2026-10-14T00:00:00")}
    names = {"C00718866": "JON OSSOFF FOR SENATE", "C00314575": "COLLINS FOR SENATOR",
             "C00541888": "KATHERINE CLARK FOR CONGRESS", "C00545749": "EMMER FOR CONGRESS"}

    def committees(url):
        c = re.search(r"/candidate/([A-Z0-9]+)/committees/", url).group(1)
        return json.dumps({"results": [{"committee_id": cid[c], "name": names[cid[c]]}]})

    def tot(url):
        c = re.search(r"/committee/([A-Z0-9]+)/totals/", url).group(1)
        return json.dumps({"results": [totals.get(c, dict(receipts=1000000, individual_itemized_contributions=500000,
                                                               individual_unitemized_contributions=200000,
                                                               other_political_committee_contributions=300000,
                                                               coverage_end_date="2026-09-30T00:00:00"))]})

    def by_state(url):
        home = "GA" if "C00718866" in url else "ME"
        return json.dumps({"results": [{"state": home, "state_full": {"GA": "Georgia", "ME": "Maine"}[home], "total": 9000000, "count": 1},
                                       {"state": "CA", "state_full": "California", "total": 7000000, "count": 1},
                                       {"state": "NY", "state_full": "New York", "total": 6000000, "count": 1},
                                       {"state": "MA", "state_full": "Massachusetts", "total": 2000000, "count": 1},
                                       {"state": "TX", "state_full": "Texas", "total": 1000000, "count": 1},
                                       {"state": "FL", "state_full": "Florida", "total": 900000, "count": 1}]})

    def by_emp(url):
        return json.dumps({"results": [{"employer": "RETIRED", "total": 5000000, "count": 1},
                                       {"employer": "MICROSOFT", "total": 48000, "count": 1},
                                       {"employer": "SELF-EMPLOYED", "total": 40000, "count": 1}]})
    return [(r"/candidate/[A-Z0-9]+/committees/", committees), (r"/committee/[A-Z0-9]+/totals/", tot),
            (r"schedule_a/by_state/", by_state), (r"schedule_a/by_employer/", by_emp)]


@pytest.fixture
def cfg(tmp_path):
    c = load_config()
    c["output_dir"] = str(tmp_path / "data")
    c["house_years"] = [2026]
    c["site_dir"] = str(tmp_path / "site")
    return c


BILL_LIST = {"bills": [
    {"congress": 119, "type": "HR", "number": "2347", "title": "Example Senate Floor Act",
     "latestAction": {"actionDate": "2026-11-06", "text": "Cloture motion on the motion to proceed to the measure presented in Senate."}},
    {"congress": 119, "type": "S", "number": "100", "title": "Already Done Act",
     "latestAction": {"actionDate": "2026-11-06", "text": "Passed Senate without amendment by Unanimous Consent."}},
    {"congress": 119, "type": "HR", "number": "55", "title": "Committee Act",
     "latestAction": {"actionDate": "2026-11-06", "text": "Referred to the House Committee on Agriculture."}},
    {"congress": 119, "type": "SRES", "number": "9", "title": "A resolution",
     "latestAction": {"actionDate": "2026-11-06", "text": "Considered by Senate."}}],
    "pagination": {"count": 4}}


def rules_all(tmp_path):
    house = {313: (FIX / "house_roll313.xml").read_bytes(), 314: (FIX / "house_roll314.xml").read_bytes()}

    def house_roll(url):
        n = int(re.search(r"roll(\d+)\.xml", url).group(1))
        return house.get(n)
    rules = [
        (r"legislators-current\.json", json.dumps(LEGISLATORS)),
        (r"legislators-district-offices\.json", json.dumps([{"id": {"bioguide": "O000174"}, "offices": [
            {"city": "Atlanta", "address": "3280 Peachtree Road NE", "suite": "Suite 2640", "zip": "30305", "phone": "470-786-7800"}]}])),
        (r"clerk\.house\.gov/evs/2026/roll\d+\.xml", house_roll),
        (r"vote_menu_119_2\.xml", (FIX / "senate_menu.xml").read_bytes()),
        (r"vote_119_2_00250\.xml", (FIX / "senate_vote250.xml").read_bytes()),
        (r"docs\.house\.gov/floor/.*20261116", HOUSE_FLOOR),
        (r"floor_schedule\.htm", SENATE_FLOOR),
        (r"/bill/119/s/4668/summaries", B4668["summaries"]), (r"/bill/119/s/4668/titles", B4668["titles"]),
        (r"/bill/119/s/4668\?", B4668["bill"]),
        (r"/bill/119/s/2403/summaries", B2403["summaries"]), (r"/bill/119/s/2403/titles", B2403["titles"]),
        (r"/bill/119/s/2403\?", B2403["bill"]),
        (r"/v3/bill/119\?", json.dumps(BILL_LIST)),
        (r"/bill/119/", lambda u: json.dumps({"bill": {"title": "Other bill"}, "summaries": [], "titles": []})),
    ] + lda_rules() + fec_api_rules()
    return rules


def test_parsers():
    v = votes.parse_house((FIX / "house_roll314.xml").read_bytes())
    assert v["bill"]["label"] == "S. 2403" and v["kind"] == "passage" and v["totals"]["yea"] == 401
    assert v["date"] == "2026-09-16" and v["by_party"]["Democratic"]["nay"] == 0
    assert votes.parse_house((FIX / "house_roll313.xml").read_bytes())["kind"] == "resolution"
    s = votes.parse_senate((FIX / "senate_vote250.xml").read_bytes(), {"S414": "O000174"})
    assert s["date"] == "2026-09-28" and s["bill"]["label"] == "S. 4668" and s["kind"] == "passage"
    assert s["totals"] == {"yea": 77, "nay": 22, "present": 0, "not_voting": 1}
    assert s["positions"][1]["id"] == "O000174"
    assert votes.classify("On the Cloture Motion", "senate", ("s", 1)) == "cloture"
    assert votes.classify("On the Nomination", "senate", None) == "nomination"
    assert votes.classify("On Motion to Recommit", "house", ("hr", 1)) == "procedural"
    assert lobbying.display_name("DUKE UNIVERSITY") == "Duke University"
    assert lobbying.display_name("NATIONAL HOCKEY LEAGUE PLAYERS ASSOCIATION") == "National Hockey League Players Association"
    assert lobbying.display_name("CHARTER COMMUNICATIONS, INC.") == "Charter Communications"
    assert fec.shares([["a", 55343417.20], ["b", 46885098.11], ["c", 1099328.60], ["d", 0]]) == \
        [["a", 54], ["b", 45], ["c", 1], ["d", 0]]
    assert lobbying.client_name("CORNERSTONE GOVERNMENT AFFAIRS OBO NATIONAL HOCKEY LEAGUE") == "NATIONAL HOCKEY LEAGUE"
    items = upcoming.senate_items(SENATE_FLOOR, 119)
    assert [i["bill"]["label"] for i in items] == ["H.R. 9340"]


def test_full_daily_run(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("API_DATA_GOV_KEY", "test-key")
    monkeypatch.delenv("LDA_API_KEY", raising=False)
    fec_dir = os.environ.get("SOLON_TEST_FEC_DIR")
    if not fec_dir:
        pytest.skip("Set SOLON_TEST_FEC_DIR to a folder with weball26.txt and itpas2.txt")
    cm = tmp_path / "cm.txt"
    cm.write_text("\n".join("|".join(r) for r in CM_ROWS) + "\n", encoding="latin-1")
    http = FakeHttp(rules_all(tmp_path))
    mem = members.build(cfg, http)
    assert len(mem) == 6 and mem[0]["contact"]["phone"]
    core.write_json(Path(cfg["output_dir"]) / "state" / "house_2026.json", {"last": 312})
    idx = votes.update_all(cfg, http, mem)
    assert {v["id"] for v in idx} == {"house-119-2-313", "house-119-2-314", "senate-119-2-250"}
    mv = json.load(open(Path(cfg["output_dir"]) / "members" / "C001101" / "votes.json"))
    assert mv["latest_passage_vote"]["bill"]["label"] == "S. 2403"
    coming = upcoming.update(cfg, http)
    assert (119, "hr", 7008) in coming and (119, "hr", 9340) in coming
    passage = {(119, "s", 2403), (119, "s", 4668)}
    bills.update(cfg, http, passage)
    b = json.load(open(Path(cfg["output_dir"]) / "bills" / "s4668-119.json"))
    assert b["short_title"] == "Protect College Sports Act of 2026" and b["summary_short"].startswith("Sets rules")
    cand_of, pacs, cmte = fec.update(cfg, http, mem, local={"weball": Path(fec_dir) / "weball26.txt",
                                                            "pas2": Path(fec_dir) / "itpas2.txt", "cm": cm})
    assert cand_of["O000174"] == "S8GA00180"
    money = json.load(open(Path(cfg["output_dir"]) / "members" / "O000174" / "money.json"))
    assert money["summary"]["receipts"] == 115904485.77
    assert money["detail"]["types"][0] == ["Individuals giving $200 or less", 54]
    assert money["detail"]["donor_employers"]["top"][0]["employer"] == "MICROSOFT"
    kinds = {t["type"]: t["total"] for t in money["pac_by_type"]}
    assert kinds["Labor union PACs"] == 10000 and kinds["Trade association PACs"] == 5500
    assert round(sum(kinds.values())) == 1006915  # direct contributions, memo rows left out
    assert money["outside_spending"]["support_total"] == 505168 and money["outside_spending"]["oppose_total"] == 723365
    lobbying.update(cfg, http, passage, cmte)
    lob = json.load(open(Path(cfg["output_dir"]) / "lobbying" / "s4668-119.json"))
    assert [g["client"] for g in lob["groups_shown"]] == ["AMAZON.COM SERVICES LLC", "NATIONAL ASSOCIATION OF BROADCASTERS",
                                                           "AMERICAN FEDERATION OF STATE COUNTY AND MUNICIPAL EMPLOYEES"]
    assert lob["report_count"] == 4  # the S. 46680 report is not counted
    cfg["landing"]["districts"] = []
    mem_e, _ = landing.build(cfg, http, mem)
    oss = mem_e[0]
    assert oss["name"] == "Sen. Jon Ossoff" and oss["vote"]["result"] == "Passed 77 to 22"
    assert oss["money"]["sectors"][0][0] == "Other PACs" and len(oss["money"]["sectors"]) == 3
    assert oss["lobbying"]["groups"] == [["Amazon", 0, 4490000], ["National Association of Broadcasters", 5500, 2700000],
                                         ["AFSCME", 10000, 570000]]
    clark = mem_e[2]
    assert clark["sample"] is False and [g[:2] for g in clark["lobbying"]["groups"]] == [["JPMorgan Chase", 10000], ["American Benefits Council", 0],
                                                              ["Principal Financial Group", 2500]]


def test_districts_real(cfg, tmp_path):
    d = os.environ.get("SOLON_TEST_CENSUS_DIR")
    if not d:
        pytest.skip("Set SOLON_TEST_CENSUS_DIR to test with the real Census files")
    from pipeline import districts
    http = FakeHttp([(r"legislators-current\.json", json.dumps(LEGISLATORS)),
                     (r"legislators-district-offices\.json", "[]")])
    mem = members.build(cfg, http)
    idx = districts.build(cfg, http, mem, local={"districts": Path(d) / "cb_2025_us_cd119_500k.zip",
                                                 "counties": Path(d) / "cb_2025_us_county_500k.zip"})
    by = {r["code"]: r for r in idx}
    assert by["LA-01"]["polsby_popper"] == pytest.approx(0.0359, abs=0.0002)
    assert by["MA-09"]["polsby_popper"] == pytest.approx(0.0459, abs=0.0002)
    assert by["LA-01"]["rank_least_compact"] == 1
    assert by["LA-01"]["rank_least_compact_in_party"] == 1
    ma = json.load(open(Path(cfg["output_dir"]) / "districts" / "MA-09.json"))
    assert ma["svg"]["islands_bridged"] and {c["county"] for c in ma["svg"]["counties"]} >= {"Barnstable", "Dukes", "Nantucket"}
    cfg["landing"]["districts"] = ["LA-01", "MA-09"]
    cfg["landing"]["house_members"] = []
    _, dis = landing.build(cfg, http, mem)
    assert dis[0]["name"] == "Louisiana District 1" and dis[0]["whole"] == {"d": 24, "r": 74}
    assert len(dis[1]["areas"]) == 6 and dis[1]["sample"] is False


def test_senate_and_schedule(cfg):
    from datetime import date
    idx = [
        {"id": "senate-119-2-257", "chamber": "senate", "congress": 119, "session": 2, "number": 257, "date": "2026-11-09",
         "question": "On Cloture on the Motion to Proceed", "kind": "cloture",
         "result": "Cloture on the Motion to Proceed Agreed to (73-25, 3/5 majority required)",
         "bill": {"type": "hr", "number": 2347, "label": "H.R. 2347", "id": "hr2347-119"},
         "totals": {"yea": 73, "nay": 25, "present": 0, "not_voting": 2}},
        {"id": "senate-119-2-240", "chamber": "senate", "congress": 119, "session": 2, "number": 240, "date": "2026-11-02",
         "question": "On the Cloture Motion", "kind": "cloture", "result": "Cloture Motion Agreed to",
         "bill": {"type": "s", "number": 7, "label": "S. 7", "id": "s7-119"}, "totals": {"yea": 70, "nay": 28}},
        {"id": "senate-119-2-245", "chamber": "senate", "congress": 119, "session": 2, "number": 245, "date": "2026-11-04",
         "question": "On Passage of the Bill", "kind": "passage", "result": "Bill Passed",
         "bill": {"type": "s", "number": 7, "label": "S. 7", "id": "s7-119"}, "totals": {"yea": 71, "nay": 27}},
        {"id": "house-119-2-316", "chamber": "house", "congress": 119, "session": 2, "number": 316, "date": "2026-11-09",
         "question": "On Motion to Suspend the Rules and Pass, as Amended", "kind": "passage", "result": "Passed",
         "bill": {"type": "hr", "number": 9612, "label": "H.R. 9612", "id": "hr9612-119"}, "totals": {"yea": 394, "nay": 3}},
        {"id": "house-119-1-10", "chamber": "house", "congress": 119, "session": 1, "number": 10, "date": "2025-01-09",
         "question": "On Passage", "kind": "passage", "result": "Passed", "bill": None, "totals": {"yea": 1, "nay": 0}},
    ]
    s = upcoming.senate_from_votes(idx, 119, today=date(2026, 11, 10))
    assert [i["bill"]["label"] for i in s] == ["H.R. 2347"]  # S. 7 already had its final vote
    assert s[0]["description"].startswith("The Senate voted 73 to 25 on November 9 to end debate on the motion to proceed")
    http = FakeHttp([(r"/v3/bill/119\?", json.dumps(BILL_LIST))])
    api = upcoming.senate_from_congress(http, "k", 119, today=date(2026, 11, 10))
    assert [i["bill"]["label"] for i in api] == ["H.R. 2347"]
    assert "sort=updateDate desc" in http.calls[0]
    merged = upcoming.merge_senate(s, api)
    assert len(merged) == 1 and merged[0]["basis"] == "senate roll call 257"
    assert landing.election_day(2026) == date(2026, 11, 3) and landing.election_day(2028) == date(2028, 11, 7)
    out = Path(cfg["output_dir"])
    core.write_json(out / "votes" / "index.json", {"count": len(idx), "votes": idx})
    core.write_json(out / "upcoming.json", {"updated": "2026-11-10", "senate": merged, "house": [
        {"bill": {"type": "hr", "number": 9612, "label": "H.R. 9612", "id": "hr9612-119"},
         "description": "American Enrichment Deployment Act, as amended", "week_of": "2026-11-09"},
        {"bill": {"type": "hr", "number": 3978, "label": "H.R. 3978", "id": "hr3978-119"},
         "description": "Nuclear REFUEL (Recycling Efficient Fuels Utilizing Expedited Licensing) Act, as amended", "week_of": "2026-11-09"},
        {"bill": {"type": "hres", "number": 1650, "label": "H.Res. 1650", "id": "hres1650-119"},
         "description": "Providing for consideration of the bill (H.R. 1013)", "week_of": "2026-11-09"},
        {"bill": {"type": "hr", "number": 1, "label": "H.R. 1", "id": "hr1-119"}, "description": "Old Week Act", "week_of": "2026-11-02"}]})
    r = landing.rollcalls(cfg, today=date(2026, 11, 10))
    assert r["months"][0] == ["Jan 2025", 1, 0] and r["months"][-1] == ["Nov 2026", 1, 3] and len(r["months"]) == 23
    assert r["meta"]["election"] == {"date": "November 3, 2026", "month": "Nov 2026", "total": 2}
    sch = landing.schedule(cfg)
    assert sch["house"]["week"] == "November 9" and sch["house"]["listed"] == 2
    assert sch["house"]["rows"] == [
        {"bill": "H.R. 9612", "title": "American Enrichment Deployment Act", "status": "Passed 394 to 3", "roll": 316},
        {"bill": "H.R. 3978", "title": "Nuclear REFUEL Act", "status": "Scheduled", "roll": None}]
    assert sch["house"]["source"].endswith("roll call 316")
    assert sch["senate"][0]["bill"] == "H.R. 2347" and sch["senate"][0]["status"] == "Expected"
    assert landing.schedule_bills(cfg) == [(119, "hr", 2347)]
