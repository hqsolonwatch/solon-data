"""Current members of Congress, with contact details.

Source: the public-domain congress-legislators project (unitedstates/congress-legislators),
which is built from official House, Senate and Bioguide records. Includes phone numbers,
office addresses and website contact forms used by the app's "contact your reps" feature.
"""
import logging

from .core import out_path, write_json

log = logging.getLogger("solon")

CURRENT = "https://unitedstates.github.io/congress-legislators/legislators-current.json"
OFFICES = "https://unitedstates.github.io/congress-legislators/legislators-district-offices.json"

PARTY = {"Democrat": "Democrat", "Republican": "Republican", "Independent": "Independent"}


def district_code(state, district):
    if district in (None, 0):
        return f"{state}-AL"
    return f"{state}-{int(district):02d}"


def build(cfg, http):
    people = http.json(CURRENT)
    offices = {o["id"]["bioguide"]: o.get("offices", []) for o in (http.json(OFFICES) or [])}
    members = []
    for p in people:
        t = p["terms"][-1]
        chamber = "senate" if t["type"] == "sen" else "house"
        m = {
            "bioguide": p["id"]["bioguide"],
            "lis": p["id"].get("lis"),
            "fec_ids": p["id"].get("fec", []),
            "name": p["name"].get("official_full") or f'{p["name"]["first"]} {p["name"]["last"]}',
            "first": p["name"].get("first"),
            "last": p["name"].get("last"),
            "party": PARTY.get(t.get("party"), t.get("party")),
            "chamber": chamber,
            "state": t["state"],
            "district": None if chamber == "senate" else district_code(t["state"], t.get("district")),
            "senate_class": t.get("class"),
            "term_start": t.get("start"),
            "term_end": t.get("end"),
            "contact": {
                "website": t.get("url"),
                "contact_form": t.get("contact_form"),
                "phone": t.get("phone"),
                "dc_office": t.get("address"),
                "district_offices": [
                    {k: o.get(k) for k in ("city", "address", "suite", "zip", "phone")}
                    for o in offices.get(p["id"]["bioguide"], [])
                ],
            },
        }
        short = f'{p["name"].get("nickname") or p["name"]["first"]} {p["name"]["last"]}'
        if p["name"].get("suffix"):
            short += f', {p["name"]["suffix"]}'
        m["short_name"] = short
        m["title"] = ("Sen. " if chamber == "senate" else "Rep. ") + short
        members.append(m)
    members.sort(key=lambda m: (m["state"], m["chamber"], m["district"] or "", m["last"]))
    write_json(out_path(cfg, "members.json"), {
        "source": "unitedstates/congress-legislators (public domain), from official House, Senate and Bioguide records",
        "source_url": CURRENT,
        "count": len(members),
        "members": members,
    })
    log.info("Members: %d", len(members))
    return members


def by_id(members):
    return {m["bioguide"]: m for m in members}
