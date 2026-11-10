"""Run the Solon Watch data pipeline.

    python -m pipeline.run daily       members, votes, bills, upcoming, money, lobbying, landing page
    python -m pipeline.run districts   district shapes and compactness (run a few times a year)
    python -m pipeline.run all         both
"""
import logging
import os
import sys
import time
import traceback
from pathlib import Path

from . import bills, districts, fec, landing, lobbying, members, upcoming, votes
from .core import LAWMAKING_TYPES, Http, load_config, out_path, read_json, write_json

log = logging.getLogger("solon")


def make_http(cfg):
    key = bool(os.environ.get("LDA_API_KEY"))
    return Http(
        min_interval={"api.open.fec.gov": 0.4, "api.congress.gov": 0.8, "lda.gov": 0.6 if key else 4.0,
                      "www.senate.gov": 0.5, "clerk.house.gov": 0.3, "api.github.com": 0.5},
        budgets={"api.open.fec.gov": cfg["fec"]["max_api_requests_per_run"], "api.congress.gov": 4000,
                 "lda.gov": 900 if key else 250})


def step(name, fn, status):
    t = time.time()
    try:
        out = fn()
        status["steps"][name] = {"ok": True, "seconds": round(time.time() - t)}
        return out
    except Exception as e:
        log.error("Step %s failed: %s", name, e)
        traceback.print_exc()
        status["steps"][name] = {"ok": False, "error": str(e)[:500], "seconds": round(time.time() - t)}
        return None


def daily(cfg, http, status):
    mem = step("members", lambda: members.build(cfg, http), status)
    if not mem:
        mem = (read_json(out_path(cfg, "members.json")) or {}).get("members")
        if not mem:
            return
        log.warning("Using yesterday's member list")
    idx = step("votes", lambda: votes.update_all(cfg, http, mem), status) or []
    passage = {(v["congress"], v["bill"]["type"], v["bill"]["number"]) for v in idx
               if v["kind"] == "passage" and v.get("bill") and v["bill"]["type"] in LAWMAKING_TYPES}
    coming = step("upcoming", lambda: upcoming.update(cfg, http), status) or []
    res = step("money", lambda: fec.update(cfg, http, mem), status)
    cmte = res[2] if res else {}
    # Bills the pre-launch page needs go first, so the page is complete after one run.
    first = []
    for m in landing.pick_members(cfg, mem):
        row = landing.latest_bill_vote(cfg, m["bioguide"])
        if row:
            first.append((cfg["congress"], row["bill"]["type"], row["bill"]["number"]))
    # Then the bills in the page's floor schedule lists, so their titles are ready.
    first += landing.schedule_bills(cfg)
    step("bills", lambda: bills.update(cfg, http, passage | set(coming), first=first), status)
    step("lobbying", lambda: lobbying.update(cfg, http, passage, cmte, first=first), status)
    if not (Path(cfg["output_dir"]) / "districts" / "index.json").exists():
        step("districts", lambda: districts.build(cfg, http, mem), status)
    step("landing", lambda: landing.build(cfg, http, mem), status)


def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    what = argv[1] if len(argv) > 1 else "daily"
    cfg = load_config()
    http = make_http(cfg)
    status = {"started": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()), "run": what, "steps": {},
              "api_keys": {"API_DATA_GOV_KEY": bool(os.environ.get("API_DATA_GOV_KEY")),
                           "LDA_API_KEY": bool(os.environ.get("LDA_API_KEY"))}}
    if what in ("districts", "all"):
        mem = step("members", lambda: members.build(cfg, http), status)
        if mem:
            step("districts", lambda: districts.build(cfg, http, mem), status)
    if what in ("daily", "all"):
        daily(cfg, http, status)
    status["requests"] = http.counts
    status["finished"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    write_json(out_path(cfg, f"status_{what}.json"), status)
    failed = [k for k, v in status["steps"].items() if not v["ok"]]
    if failed:
        log.error("Finished with problems in: %s", ", ".join(failed))
        return 1
    log.info("Finished without problems")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
