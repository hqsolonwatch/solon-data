"""Campaign money from the Federal Election Commission (FEC).

Two kinds of FEC data:
- Bulk files (no key needed, refreshed daily by the FEC):
    weball{yy}.zip  one row of totals per candidate
    pas2{yy}.zip    every contribution from a committee (PAC, party, etc.) to a candidate
    cm{yy}.zip      committee names and the companies or groups they are connected to
- The FEC API (needs API_DATA_GOV_KEY): donor-size split, where donors live, where they work.
"""
import io
import logging
import os
import time
import zipfile

from .core import ROOT, Budget, out_path, read_json, write_json

log = logging.getLogger("solon")

BULK = "https://www.fec.gov/files/bulk-downloads/{cycle}/{name}{yy}.zip"
API = "https://api.open.fec.gov/v1"
CACHE = ROOT / ".cache"

WEBALL_COLS = ["CAND_ID", "CAND_NAME", "CAND_ICI", "PTY_CD", "CAND_PTY_AFFILIATION", "TTL_RECEIPTS",
               "TRANS_FROM_AUTH", "TTL_DISB", "TRANS_TO_AUTH", "COH_BOP", "COH_COP", "CAND_CONTRIB",
               "CAND_LOANS", "OTHER_LOANS", "CAND_LOAN_REPAY", "OTHER_LOAN_REPAY", "DEBTS_OWED_BY",
               "TTL_INDIV_CONTRIB", "CAND_OFFICE_ST", "CAND_OFFICE_DISTRICT", "SPEC_ELECTION",
               "PRIM_ELECTION", "RUN_ELECTION", "GEN_ELECTION", "GEN_ELECTION_PRECENT",
               "OTHER_POL_CMTE_CONTRIB", "POL_PTY_CONTRIB", "CVG_END_DT", "INDIV_REFUNDS", "CMTE_REFUNDS"]
PAS2_COLS = ["CMTE_ID", "AMNDT_IND", "RPT_TP", "TRANSACTION_PGI", "IMAGE_NUM", "TRANSACTION_TP", "ENTITY_TP",
             "NAME", "CITY", "STATE", "ZIP_CODE", "EMPLOYER", "OCCUPATION", "TRANSACTION_DT", "TRANSACTION_AMT",
             "OTHER_ID", "CAND_ID", "TRAN_ID", "FILE_NUM", "MEMO_CD", "MEMO_TEXT", "SUB_ID"]
CM_COLS = ["CMTE_ID", "CMTE_NM", "TRES_NM", "CMTE_ST1", "CMTE_ST2", "CMTE_CITY", "CMTE_ST", "CMTE_ZIP",
           "CMTE_DSGN", "CMTE_TP", "CMTE_PTY_AFFILIATION", "CMTE_FILING_FREQ", "ORG_TP", "CONNECTED_ORG_NM",
           "CAND_ID"]

# Direct contributions from a committee to a candidate's campaign (24K) and in-kind (24Z).
CONTRIB_TYPES = {"24K", "24Z"}

NOT_AN_EMPLOYER = {"RETIRED", "NOT EMPLOYED", "NONE", "SELF-EMPLOYED", "SELF EMPLOYED", "SELF", "N/A", "NA",
                   "HOMEMAKER", "UNEMPLOYED", "INFORMATION REQUESTED", "INFORMATION REQUESTED PER BEST EFFORTS",
                   "REQUESTED", "STUDENT", "DISABLED", "NOT APPLICABLE", "NULL", ""}


# ---------------- Bulk files ----------------

def bulk_lines(http, cfg, name, local=None):
    """Yield split rows of an FEC bulk file. `local` lets tests use a file already on disk."""
    if local:
        with open(local, encoding="latin-1") as f:
            for line in f:
                yield line.rstrip("\n").split("|")
        return
    cycle = cfg["election_cycle"]
    yy = str(cycle)[-2:]
    url = BULK.format(cycle=cycle, name=name, yy=yy)
    CACHE.mkdir(exist_ok=True)
    zpath = CACHE / f"{name}{yy}.zip"
    if not zpath.exists() or time.time() - zpath.stat().st_mtime > 20 * 3600:
        r = http.get(url, timeout=300)
        zpath.write_bytes(r.content)
    with zipfile.ZipFile(zpath) as z:
        inner = z.namelist()[0]
        with z.open(inner) as f:
            for line in io.TextIOWrapper(f, encoding="latin-1"):
                yield line.rstrip("\n").split("|")


def load_weball(http, cfg, local=None):
    out = {}
    for r in bulk_lines(http, cfg, "weball", local):
        if len(r) < len(WEBALL_COLS):
            continue
        d = dict(zip(WEBALL_COLS, r))
        out[d["CAND_ID"]] = d
    return out


def load_committees(http, cfg, local=None):
    out = {}
    for r in bulk_lines(http, cfg, "cm", local):
        if len(r) < len(CM_COLS):
            continue
        d = dict(zip(CM_COLS, r))
        out[d["CMTE_ID"]] = d
    return out


def load_pac_contributions(http, cfg, cand_ids, local=None):
    """One pass over the committee-to-candidate file. Returns two dicts keyed by candidate ID:
    direct contributions {committee_id: dollars}, and outside spending
    {committee_id: {"support": dollars, "oppose": dollars}} (independent expenditures)."""
    wanted = set(cand_ids)
    direct, outside = {}, {}
    for r in bulk_lines(http, cfg, "pas2", local):
        if len(r) < len(PAS2_COLS):
            continue
        cand, tp = r[16], r[5]
        if cand not in wanted or r[19] == "X":
            continue
        try:
            amt = float(r[14])
        except ValueError:
            continue
        if tp in CONTRIB_TYPES:
            d = direct.setdefault(cand, {})
            d[r[0]] = d.get(r[0], 0) + amt
        elif tp in ("24E", "24A"):
            o = outside.setdefault(cand, {}).setdefault(r[0], {"support": 0.0, "oppose": 0.0})
            o["support" if tp == "24E" else "oppose"] += amt
    return direct, outside


def committee_kind(c):
    """Plain-language type of a committee that gave money, from the FEC committee file."""
    if not c:
        return "Other PACs"
    if c.get("CMTE_TP") in ("X", "Y", "Z"):
        return "Party committees"
    if c.get("CMTE_TP") in ("H", "S", "P"):
        return "Other candidates’ campaigns"
    if c.get("CMTE_DSGN") == "D":
        return "Leadership PACs (run by other politicians)"
    return {"C": "Corporate PACs", "W": "Corporate PACs", "T": "Trade association PACs", "L": "Labor union PACs",
            "M": "Membership group PACs", "V": "Cooperative PACs"}.get(c.get("ORG_TP"), "Other PACs")


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def pick_candidate_id(member, weball):
    """A member can have several FEC IDs over a career. Use the one for their current office."""
    prefix = "S" if member["chamber"] == "senate" else "H"
    ids = [i for i in member.get("fec_ids", []) if i.startswith(prefix) and i[2:4] == member["state"]]
    if not ids:
        return None
    ids.sort(key=lambda i: (i in weball, f(weball.get(i, {}).get("TTL_RECEIPTS"))), reverse=True)
    return ids[0]


def shares(parts):
    """Whole-number percentages that add up to 100 (largest remainder)."""
    tot = sum(v for _, v in parts)
    if tot <= 0:
        return [[k, 0] for k, _ in parts]
    raw = [100 * v / tot for _, v in parts]
    fl = [int(x) for x in raw]
    for i in sorted(range(len(raw)), key=lambda i: -(raw[i] - fl[i]))[:100 - sum(fl)]:
        fl[i] += 1
    return [[parts[i][0], fl[i]] for i in range(len(parts))]


# ---------------- API ----------------

def api(http, path, **params):
    params["api_key"] = os.environ["API_DATA_GOV_KEY"]
    return http.json(API + path, params=params)


def fetch_money_detail(http, cfg, member, cand_id):
    cycle = cfg["election_cycle"]
    top_n = cfg["fec"].get("top_n", 10)
    cm = api(http, f"/candidate/{cand_id}/committees/", cycle=cycle, designation="P", per_page=5)
    res = (cm or {}).get("results") or []
    if not res:
        return None
    cid = res[0]["committee_id"]
    t = ((api(http, f"/committee/{cid}/totals/", cycle=cycle, per_page=1) or {}).get("results") or [{}])[0]
    st = (api(http, "/schedules/schedule_a/by_state/", committee_id=cid, cycle=cycle, per_page=100,
              sort="-total") or {}).get("results") or []
    em = (api(http, "/schedules/schedule_a/by_employer/", committee_id=cid, cycle=cycle, per_page=60,
              sort="-total") or {}).get("results") or []
    small = f(t.get("individual_unitemized_contributions"))
    large = f(t.get("individual_itemized_contributions"))
    pac = f(t.get("other_political_committee_contributions")) + f(t.get("political_party_committee_contributions"))
    own = f(t.get("candidate_contribution")) + f(t.get("loans_made_by_candidate"))
    states = [{"state": s.get("state"), "state_full": s.get("state_full"), "total": round(f(s.get("total")), 2),
               "count": s.get("count")} for s in st if s.get("state")]
    states.sort(key=lambda s: -s["total"])
    itemized_total = sum(s["total"] for s in states)
    home = next((s["total"] for s in states if s["state"] == member["state"]), 0.0)
    employers = [{"employer": e.get("employer"), "total": round(f(e.get("total")), 2), "count": e.get("count")}
                 for e in em if (e.get("employer") or "").strip().upper() not in NOT_AN_EMPLOYER]
    employers.sort(key=lambda e: -e["total"])
    return {
        "committee_id": cid, "committee_name": res[0].get("name"),
        "coverage_start": t.get("coverage_start_date"), "coverage_end": t.get("coverage_end_date"),
        "receipts": round(f(t.get("receipts")), 2),
        "amounts": {"individuals_200_or_less": small, "individuals_over_200": large,
                    "pacs_and_parties": pac, "candidate_own": own,
                    "transfers_from_authorized": f(t.get("transfers_from_other_authorized_committee")),
                    "other_receipts": f(t.get("other_receipts"))},
        "types": shares([["Individuals giving $200 or less", small], ["Individuals giving more than $200", large],
                         ["PACs and party committees", pac], ["Candidate’s own money", own]]),
        "donor_states": {"itemized_total": round(itemized_total, 2),
                         "home_state": member["state"], "home_state_total": round(home, 2),
                         "home_state_share": round(100 * home / itemized_total) if itemized_total else None,
                         "top": states[:top_n],
                         "note": "Itemized donations from individuals (over $200), by the donor's state."},
        "donor_employers": {"top": employers[:top_n],
                            "note": "Itemized donations from individuals (over $200), grouped by the employer "
                                    "donors listed, as spelled in FEC records. Retired, self-employed and "
                                    "not-employed entries are left out."},
    }


# ---------------- Build ----------------

def update(cfg, http, members, local=None):
    """local: optional dict of bulk file paths for testing, e.g. {"weball": "...", "pas2": "...", "cm": "..."}."""
    local = local or {}
    weball = load_weball(http, cfg, local.get("weball"))
    cmte = load_committees(http, cfg, local.get("cm")) if (local.get("cm") or not local) else {}
    cand_of = {m["bioguide"]: pick_candidate_id(m, weball) for m in members}
    pacs, outside = load_pac_contributions(http, cfg, [c for c in cand_of.values() if c], local.get("pas2"))

    have_key = bool(os.environ.get("API_DATA_GOV_KEY"))
    refresh = cfg["fec"].get("refresh_days", 7) * 86400
    now = time.time()
    # Refresh the members whose details are oldest first.
    first = set(cfg.get("landing", {}).get("house_members", []))
    for party in ("Democrat", "Republican"):
        sens = [m for m in members if m["chamber"] == "senate" and m["party"] == party and cand_of[m["bioguide"]]]
        sens.sort(key=lambda m: -f(weball.get(cand_of[m["bioguide"]], {}).get("TTL_RECEIPTS")))
        first.update(m["bioguide"] for m in sens[:1])
    order = sorted(members, key=lambda m: (m["bioguide"] not in first,
                   (read_json(out_path(cfg, "members", m["bioguide"], "money.json"), {}) or {})
                   .get("detail_fetched_ts", 0)))
    api_stopped = False
    for m in order:
        cand = cand_of[m["bioguide"]]
        path = out_path(cfg, "members", m["bioguide"], "money.json")
        old = read_json(path, {}) or {}
        w = weball.get(cand, {}) if cand else {}
        given = pacs.get(cand, {})
        pac_rows = sorted(({"committee_id": k, "name": cmte.get(k, {}).get("CMTE_NM"),
                            "connected_org": cmte.get(k, {}).get("CONNECTED_ORG_NM") or None,
                            "total": round(v, 2)} for k, v in given.items()), key=lambda r: -r["total"])
        kinds = {}
        for k, v in given.items():
            kinds[committee_kind(cmte.get(k))] = kinds.get(committee_kind(cmte.get(k)), 0) + v
        by_type = [{"type": k, "total": round(v, 2)} for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])]
        osp = outside.get(cand, {})
        spenders = sorted(({"committee_id": k, "name": cmte.get(k, {}).get("CMTE_NM"),
                            "support": round(v["support"], 2), "oppose": round(v["oppose"], 2)}
                           for k, v in osp.items()), key=lambda r: -(r["support"] + r["oppose"]))
        out_sp = {"support_total": round(sum(v["support"] for v in osp.values()), 2),
                  "oppose_total": round(sum(v["oppose"] for v in osp.values()), 2),
                  "spenders": spenders[:25],
                  "note": "Independent expenditures: money outside groups such as super PACs spent on ads and "
                          "other efforts to support or oppose this candidate, without coordinating with a campaign."}
        rec = {
            "bioguide": m["bioguide"], "name": m["name"], "cycle": cfg["election_cycle"],
            "candidate_id": cand,
            "summary": None if not w else {
                "receipts": f(w["TTL_RECEIPTS"]), "individual_contributions": f(w["TTL_INDIV_CONTRIB"]),
                "pac_contributions": f(w["OTHER_POL_CMTE_CONTRIB"]), "party_contributions": f(w["POL_PTY_CONTRIB"]),
                "candidate_contributions": f(w["CAND_CONTRIB"]), "candidate_loans": f(w["CAND_LOANS"]),
                "transfers_from_authorized": f(w["TRANS_FROM_AUTH"]), "cash_on_hand": f(w["COH_COP"]),
                "coverage_end": w["CVG_END_DT"]},
            "pac_contributions": pac_rows,
            "pac_by_type": by_type,
            "outside_spending": out_sp,
            "detail": old.get("detail"), "detail_fetched_ts": old.get("detail_fetched_ts", 0),
            "sources": {"summary": "FEC candidate summary file (weball)",
                        "pac_contributions": "FEC contributions from committees to candidates (pas2), "
                                             "direct and in-kind contributions; outside spending from "
                                             "independent expenditures in the same file",
                        "detail": "FEC API, principal campaign committee"},
        }
        if cand and have_key and not api_stopped and now - rec["detail_fetched_ts"] > refresh:
            try:
                d = fetch_money_detail(http, cfg, m, cand)
                if d:
                    rec["detail"], rec["detail_fetched_ts"] = d, now
            except Budget:
                api_stopped = True
                log.info("FEC API: request budget for this run used up; the rest refresh next run")
            except Exception as e:
                log.warning("FEC API failed for %s: %s", m["name"], e)
        write_json(path, rec)
    rankings(cfg, members, cand_of, weball)
    races(cfg, weball, members)
    log.info("FEC: money files written for %d members", len(members))
    return cand_of, pacs, cmte


def rankings(cfg, members, cand_of, weball):
    rows = []
    for m in members:
        w = weball.get(cand_of.get(m["bioguide"]) or "")
        if w:
            rows.append({"bioguide": m["bioguide"], "name": m["name"], "party": m["party"], "chamber": m["chamber"],
                         "state": m["state"], "district": m["district"], "receipts": f(w["TTL_RECEIPTS"]),
                         "coverage_end": w["CVG_END_DT"]})
    out = {}
    for chamber in ("senate", "house"):
        for party in ("Democrat", "Republican", "Independent"):
            r = sorted((x for x in rows if x["chamber"] == chamber and x["party"] == party), key=lambda x: -x["receipts"])
            out[f"{chamber}_{party.lower()}"] = r[:25]
    write_json(out_path(cfg, "rankings.json"), {"cycle": cfg["election_cycle"],
                                                "source": "FEC candidate summary file (weball)", "top_fundraisers": out})


ICI = {"I": "Incumbent", "C": "Challenger", "O": "Open seat"}
PARTY_CODE = {"DEM": "Democrat", "REP": "Republican", "IND": "Independent", "LIB": "Libertarian", "GRE": "Green"}


def races(cfg, weball, members):
    """Every federal candidate who has filed money reports this cycle, grouped by race."""
    sitting = {}
    for m in members:
        for i in m.get("fec_ids", []):
            sitting[i] = m["bioguide"]
    out = {}
    for cid, w in weball.items():
        office = cid[0]
        if office not in ("S", "H"):
            continue
        st = w["CAND_OFFICE_ST"]
        dist = w["CAND_OFFICE_DISTRICT"] or "00"
        race = f"{st}-SEN" if office == "S" else (f"{st}-AL" if dist == "00" else f"{st}-{int(dist):02d}")
        out.setdefault(race, []).append({
            "candidate_id": cid, "name": w["CAND_NAME"], "party": PARTY_CODE.get(w["CAND_PTY_AFFILIATION"],
                                                                           w["CAND_PTY_AFFILIATION"]),
            "status": ICI.get(w["CAND_ICI"], w["CAND_ICI"]), "bioguide": sitting.get(cid),
            "receipts": f(w["TTL_RECEIPTS"]), "cash_on_hand": f(w["COH_COP"]), "coverage_end": w["CVG_END_DT"]})
    for r in out.values():
        r.sort(key=lambda c: -c["receipts"])
    write_json(out_path(cfg, "races.json"), {
        "cycle": cfg["election_cycle"],
        "note": "Candidates who filed campaign finance reports with the FEC for this two-year period. Senate "
                "candidates are listed even when their seat is not up this cycle.",
        "source": "FEC candidate summary file (weball)", "races": dict(sorted(out.items()))})
