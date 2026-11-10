"""Builds the example data for the Solon Watch pre-launch page.

Writes site/landing-data.js (the MEMBERS and DISTRICTS lists) and, if site/index.template.html
exists, a ready-to-use site/index.html with those lists filled in.
"""
import json
import logging
import re
from datetime import date
from pathlib import Path

from shapely.geometry import Polygon

from . import results
from .core import LAWMAKING_TYPES, ROOT, read_json
from .districts import place_labels

log = logging.getLogger("solon")

STATE_NAMES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
               "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
               "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
               "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
               "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
               "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
               "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
               "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
               "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
               "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
               "DC": "District of Columbia", "PR": "Puerto Rico"}

ADJ = {"Democrat": "Democratic", "Republican": "Republican"}

PLACEHOLDER_LOBBY = {"cycle": "2026 cycle",
                     "groups": [["[Organization A]", 0], ["[Organization B]", 0], ["[Organization C]", 0]],
                     "source": "[Lobbying source]"}


def smart_title(s):
    small = {"of", "and", "the", "for", "in", "on", "at", "to", "a"}
    words = (s or "").lower().split()
    return " ".join(w if i and w in small else w[:1].upper() + w[1:] for i, w in enumerate(words))


def money_text(x):
    if x >= 1_000_000:
        return f"${x / 1_000_000:.1f} million"
    return f"${x:,.0f}"


def long_date(iso):
    d = date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def latest_bill_vote(cfg, bioguide):
    v = read_json(Path(cfg["output_dir"]) / "members" / bioguide / "votes.json") or {}
    for r in v.get("votes", []):
        if r["kind"] == "passage" and r.get("bill") and r["bill"]["type"] in LAWMAKING_TYPES:
            return r
    return None


def vote_block(cfg, row):
    vote = read_json(Path(cfg["output_dir"]) / "votes" / row["vote_id"].split("-")[0] /
                     ("-".join(row["vote_id"].split("-")[1:]) + ".json"))
    b = read_json(Path(cfg["output_dir"]) / "bills" / f'{row["bill"]["id"]}.json') or {}
    t = vote["totals"]
    passed = bool(re.search(r"pass|agreed", vote["result"] or "", re.I))
    if vote["chamber"] == "senate":
        src = f'senate.gov, roll call vote {vote["number"]}, {ordinal(vote["congress"])} Congress, session {vote["session"]}'
    else:
        src = f'clerk.house.gov, roll call {vote["number"]}, {vote["date"][:4]}'
    return {
        "bill": row["bill"]["label"],
        "title": b.get("short_title") or b.get("title") or "[Bill title]",
        "summary": b.get("summary_short") or "[One plain-English sentence on what the bill does.]",
        "cast": row["vote"],
        "result": f'{"Passed" if passed else "Failed"} {t["yea"]} to {t["nay"]}',
        "date": long_date(vote["date"]),
        "source": src,
    }, bool(b.get("summary_short"))


def lobbying_block(cfg, bill_id, money):
    lob = read_json(Path(cfg["output_dir"]) / "lobbying" / f"{bill_id}.json")
    if not lob or not lob.get("groups_shown"):
        return dict(PLACEHOLDER_LOBBY), False
    gave = {r["committee_id"]: r["total"] for r in (money or {}).get("pac_contributions", [])}
    groups, pac_list = [], []
    for g in lob["groups_shown"]:
        amt = g.get("latest_report_amount")
        groups.append([g.get("display_name") or g["client"],
                       round(sum(gave.get(i, 0) for i in g["pac_ids"])),
                       None if amt is None else round(amt)])
        pac_list.append(f'{g.get("display_name") or g["client"]} ({", ".join(g["pac_ids"])})')
    src = (f"Lobbying reports naming this bill, filed under the Lobbying Disclosure Act (lda.gov). "
           f"Shown: up to {cfg['lobbying']['groups_per_bill']} groups with a federal PAC, ranked by the amount "
           f"on their latest report naming the bill. Lobbying amounts are that report's total, across all issues. Donations: FEC contributions from committees to "
           f"candidates, {cfg['election_cycle'] - 1} to {cfg['election_cycle']}, from each group's PAC: " + "; ".join(pac_list))
    return {"cycle": f"{cfg['election_cycle']} cycle", "groups": groups, "source": src}, True


def money_block(cfg, money):
    d = (money or {}).get("detail")
    if not d:
        return None, False
    end = d.get("coverage_end", "")[:10]
    tr = d["amounts"].get("transfers_from_authorized", 0)
    types_src = (f'FEC, {smart_title(d.get("committee_name") or "principal campaign committee")} '
                 f'(committee {d["committee_id"]}), Jan. 1, {cfg["election_cycle"] - 1} to '
                 f'{long_date(end) if end else "latest report"}. Shares are of money given directly by individuals, '
                 f'PACs, party committees and the candidate.')
    if tr:
        types_src += (f" Transfers from joint fundraising committees ({money_text(tr)}) and other receipts are "
                      f"counted in the total but not in the shares")
    mode = cfg["landing"].get("money_breakdown", "pac_types")
    if mode == "employers":
        top = [[e["employer"].title(), round(e["total"])] for e in d["donor_employers"]["top"][:5]]
        top_src = "FEC, itemized donations from individuals (over $200), by the donor's employer"
    elif mode == "states":
        top = [[s["state_full"] or s["state"], round(s["total"])] for s in d["donor_states"]["top"][:5]]
        share = d["donor_states"].get("home_state_share")
        top_src = ("FEC, itemized donations from individuals (over $200), by the donor's state" +
                   (f". {share}% came from the member's home state" if share is not None else ""))
    elif mode == "outside":
        o = money.get("outside_spending") or {}
        top = []
        for r in o.get("spenders", []):
            for side, word in (("support", "supporting"), ("oppose", "opposing")):
                if r[side] > 0:
                    top.append([f'{smart_title(r["name"] or r["committee_id"])} ({word})', round(r[side])])
        top = sorted(top, key=lambda t: -t[1])[:5]
        top_src = (f'FEC, independent expenditures reported by outside groups, {cfg["election_cycle"] - 1} to '
                   f'{cfg["election_cycle"]}. Outside groups spent {money_text(o.get("support_total", 0))} supporting '
                   f'and {money_text(o.get("oppose_total", 0))} opposing this candidate. This money is not given to '
                   f'the campaign and is not part of the total above')
    else:
        top = [[t["type"], round(t["total"])] for t in (money.get("pac_by_type") or [])[:5]]
        top_src = (f'FEC, contributions from committees to this candidate, {cfg["election_cycle"] - 1} to '
                   f'{cfg["election_cycle"]}, as reported by the committees that gave. Each committee’s type comes '
                   f'from its FEC registration')
    if not top:
        return None, False
    return {"cycle": f'{cfg["election_cycle"]} cycle', "total": money_text(d["receipts"]),
            "types": d["types"], "typesSource": types_src, "sectors": top, "sectorsSource": top_src}, True


def member_entry(cfg, m):
    money = read_json(Path(cfg["output_dir"]) / "members" / m["bioguide"] / "money.json")
    row = latest_bill_vote(cfg, m["bioguide"])
    complete = True
    if row:
        vote, ok = vote_block(cfg, row)
        complete &= ok
        lob, ok = lobbying_block(cfg, row["bill"]["id"], money)
        complete &= ok
    else:
        vote = {"bill": "[Bill]", "title": "[Bill title]", "summary": "[Summary]", "cast": "[Vote]",
                "result": "[Result]", "date": "[Date]", "source": "[Source]"}
        lob, complete = dict(PLACEHOLDER_LOBBY), False
    e = {"sample": True, "party": m["party"], "chamber": m["chamber"], "name": m["title"],
         "role": (f"U.S. Senator, {STATE_NAMES.get(m['state'], m['state'])}" if m["chamber"] == "senate" else
                  f"U.S. House, {STATE_NAMES.get(m['state'], m['state'])} District {int(m['district'][-2:]) if m['district'][-2:].isdigit() else 'At Large'}"),
         "vote": vote, "lobbying": lob}
    if m["chamber"] == "senate":
        mb, ok = money_block(cfg, money)
        e["money"] = mb or {"cycle": f'{cfg["election_cycle"]} cycle', "total": "[Total]",
                            "types": [["Individuals giving $200 or less", 0], ["Individuals giving more than $200", 0],
                                      ["PACs and party committees", 0], ["Candidate’s own money", 0]],
                            "typesSource": "[Source]", "sectors": [["[Item]", 0]], "sectorsSource": "[Source]"}
        complete &= ok
    e["sample"] = not complete
    return e


def pick_members(cfg, members):
    by = {m["bioguide"]: m for m in members}
    rk = (read_json(Path(cfg["output_dir"]) / "rankings.json") or {}).get("top_fundraisers", {})
    out = []
    for key in ("senate_democrat", "senate_republican"):
        top = rk.get(key) or []
        if top:
            out.append(by[top[0]["bioguide"]])
    out += [by[b] for b in cfg["landing"].get("house_members", []) if b in by]
    return out


def district_entry(cfg, http, code):
    d = read_json(Path(cfg["output_dir"]) / "districts" / f"{code}.json")
    if not d:
        return None
    idx = {r["code"]: r for r in (read_json(Path(cfg["output_dir"]) / "districts" / "index.json") or {}).get("districts", [])}
    r = idx.get(code, {})
    party = (d["member"] or {}).get("party", "")
    res = results.district_results(http, cfg, code, d["svg"]["counties"])
    svg_by_fips = {c["fips"]: c for c in d["svg"]["counties"]}
    svg_by_name = {results.norm_county(c["county"]): c for c in d["svg"]["counties"]}
    areas = []
    if res:
        for a in res["areas"]:
            c = svg_by_fips.get(a.get("fips")) or svg_by_name.get(results.norm_county(a["name"]))
            if c is None:
                continue
            areas.append({"name": a["name"], "d": a["d"], "r": a["r"], "poly": c["poly"], "lx": 0, "ly": 0})
        ring = [tuple(map(float, p.split(","))) for p in d["svg"]["shape"].split()]
        place_labels(areas, Polygon(ring).buffer(0))
    rank_txt = ""
    if r.get("rank_least_compact"):
        rank_txt = (f". It ranks {r['rank_least_compact']} of the districts in states with more than one seat "
                    f"(1 is the least compact), and {r.get('rank_least_compact_in_party')} among districts held by a "
                    f"{party}")
    st, num = code.split("-")
    return {
        "sample": not bool(res and areas),
        "party": party,
        "label": f"One of the least compact {ADJ[party]}-held districts" if party in ADJ
                 else "One of the least compact districts",
        "name": f"{STATE_NAMES.get(st, st)} District {int(num) if num.isdigit() else 'At Large'}",
        "member": (d["member"] or {}).get("name") and "Rep. " + (d["member"].get("short_name") or d["member"]["name"]),
        "score": f'{d["polsby_popper"]:.2f}',
        "scoreSource": (f'Polsby-Popper score of {d["polsby_popper"]:.3f}, calculated by Solon Watch from the U.S. '
                        f'Census Bureau cartographic boundary file for the {ordinal(d["congress"])} Congress '
                        f'(1:500,000), using an equal-area projection for each district' + rank_txt),
        "race": f'{cfg["districts"]["results_election_date"][:4]} U.S. House race',
        "whole": {"d": res["whole"]["d"], "r": res["whole"]["r"]} if res else {"d": 0, "r": 0},
        "shape": d["svg"]["shape"],
        "areas": areas,
        "resultsSource": res["source"] if res else "[Results not available yet]",
    }


def pick_districts(cfg):
    want = cfg["landing"].get("districts", "auto")
    if want != "auto":
        return list(want)
    idx = (read_json(Path(cfg["output_dir"]) / "districts" / "index.json") or {}).get("districts", [])
    out = []
    for party in ("Republican", "Democrat"):
        held = [r for r in idx if r.get("rank_least_compact_in_party") == 1 and (r["member"] or {}).get("party") == party]
        out += [held[0]["code"]] if held else []
    return out


def js_list(name, items):
    return f"var {name} = " + json.dumps(items, ensure_ascii=False, indent=2) + ";"


def build(cfg, http, members):
    mem = [member_entry(cfg, m) for m in pick_members(cfg, members)]
    dis = [x for x in (district_entry(cfg, http, c) for c in pick_districts(cfg)) if x]
    site = Path(cfg.get("site_dir") or ROOT / "site")
    site.mkdir(parents=True, exist_ok=True)
    tpl = ROOT / "site" / "index.template.html"
    js = js_list("MEMBERS", mem) + "\n\n" + js_list("DISTRICTS", dis) + "\n"
    (site / "landing-data.js").write_text(js, encoding="utf-8")
    if tpl.exists():
        h = tpl.read_text(encoding="utf-8")
        s1 = h.index("var MEMBERS = ["); e1 = h.index("\n];", s1) + 3
        h = h[:s1] + js_list("MEMBERS", mem) + h[e1:]
        s2 = h.index("var DISTRICTS = ["); e2 = h.index("\n];", s2) + 3
        h = h[:s2] + js_list("DISTRICTS", dis) + h[e2:]
        # The second money list is no longer industry totals, so update its heading and source line.
        mode = cfg["landing"].get("money_breakdown", "pac_types")
        heading, where = {
            "employers": ("Top donor employers", ", including where donors work, from the itemized donation "
                                                 "records campaigns file with the FEC."),
            "states": ("Where donors live", ", including where donors live, from the itemized donation records "
                                            "campaigns file with the FEC."),
            "outside": ("Outside spending", ". Outside spending comes from the independent expenditure reports "
                                            "that super PACs and other groups file with the FEC."),
        }.get(mode, ("PAC money by type", ". PAC money comes from the contribution reports PACs file with the "
                                          "FEC, and each PAC’s type comes from its FEC registration."))
        h = h.replace("'<p class=\"subhead\">Top industries</p>'", "'<p class=\"subhead\">" + heading + "</p>'")
        h = re.sub(r", and industry totals from <a href=\"https://www\.opensecrets\.org/\">OpenSecrets</a>, which sorts FEC "
                   r"donor records by industry\.", where, h)
        (site / "index.html").write_text(h, encoding="utf-8")
    log.info("Landing page data: %d members, %d districts", len(mem), len(dis))
    return mem, dis
