"""Shared helpers: settings, web requests, and writing output files."""
import json
import logging
import os
import re
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("solon")

USER_AGENT = "SolonWatch-data-pipeline/1.0 (+https://solonwatch.com)"


def load_config(path=None):
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["output_dir"] = str(ROOT / cfg.get("output_dir", "data"))
    return cfg


def out_path(cfg, *parts):
    p = Path(cfg["output_dir"]).joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


class Budget(Exception):
    """Raised when a source's request budget for this run is used up."""


class Http:
    """A polite web client: retries, waits between requests, and counts them."""

    def __init__(self, min_interval=None, budgets=None):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.min_interval = min_interval or {}   # host -> seconds between calls
        self.budgets = dict(budgets or {})        # host -> max calls this run
        self.counts = {}
        self._last = {}

    def _host(self, url):
        m = re.match(r"https?://([^/]+)", url)
        return m.group(1) if m else ""

    def get(self, url, params=None, headers=None, allow_404=False, timeout=60, retries=4):
        host = self._host(url)
        if host in self.budgets and self.counts.get(host, 0) >= self.budgets[host]:
            raise Budget(host)
        wait = self.min_interval.get(host, 0)
        for attempt in range(retries + 1):
            gap = time.time() - self._last.get(host, 0)
            if gap < wait:
                time.sleep(wait - gap)
            self._last[host] = time.time()
            self.counts[host] = self.counts.get(host, 0) + 1
            try:
                r = self.s.get(url, params=params, headers=headers, timeout=timeout)
            except requests.RequestException as e:
                log.warning("Request failed (%s), try %d: %s", e.__class__.__name__, attempt + 1, url)
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 404 and allow_404:
                return None
            if r.status_code == 429 or r.status_code >= 500:
                pause = int(r.headers.get("Retry-After", 0) or 0) or 5 * 2 ** attempt
                log.warning("HTTP %d from %s, waiting %ds", r.status_code, host, pause)
                time.sleep(min(pause, 120))
                continue
            r.raise_for_status()
            return r
        raise RuntimeError(f"Gave up after {retries + 1} tries: {url}")

    def json(self, url, **kw):
        r = self.get(url, **kw)
        return None if r is None else r.json()


def norm_name(s):
    """Normalize an organization name for matching across data sources."""
    s = (s or "").upper()
    s = s.replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    stop = {"THE", "INC", "INCORPORATED", "LLC", "LLP", "LP", "CORP", "CORPORATION", "CO", "COMPANY",
            "LTD", "PLC", "HOLDINGS", "GROUP", "OF", "AMERICA", "USA", "US", "NA", "SERVICES"}
    words = [w for w in s.split() if w not in stop]
    return " ".join(words)


BILL_TYPES = {
    # how a bill type is written -> congress.gov API code
    "HR": "hr", "S": "s", "HJRES": "hjres", "SJRES": "sjres", "HCONRES": "hconres",
    "SCONRES": "sconres", "HRES": "hres", "SRES": "sres",
}
BILL_LABEL = {"hr": "H.R.", "s": "S.", "hjres": "H.J.Res.", "sjres": "S.J.Res.", "hconres": "H.Con.Res.",
              "sconres": "S.Con.Res.", "hres": "H.Res.", "sres": "S.Res."}
LAWMAKING_TYPES = {"hr", "s", "hjres", "sjres"}   # types that can become law

_BILL_RE = re.compile(
    r"(?<!\.)\b(H\.?\s?R|S|H\.?\s?J\.?\s?RES|S\.?\s?J\.?\s?RES|H\.?\s?CON\.?\s?RES|S\.?\s?CON\.?\s?RES|H\.?\s?RES|S\.?\s?RES)\.?\s?(\d{1,5})\b",
    re.I)


def parse_bill(text):
    """Turn 'H.R. 1234', 'HR1234', 'S 4668' etc. into ('hr', 1234). Returns None if no bill."""
    m = _BILL_RE.search(text or "")
    if not m:
        return None
    t = re.sub(r"[^A-Z]", "", m.group(1).upper())
    t = BILL_TYPES.get(t)
    return (t, int(m.group(2))) if t else None


def find_bills(text):
    out = []
    for m in _BILL_RE.finditer(text or ""):
        t = BILL_TYPES.get(re.sub(r"[^A-Z]", "", m.group(1).upper()))
        if t:
            out.append((t, int(m.group(2))))
    return out


def bill_id(congress, btype, number):
    return f"{btype}{number}-{congress}"


def bill_label(btype, number):
    return f"{BILL_LABEL.get(btype, btype.upper())} {number}"


def bill_mention_regex(btype, number):
    """Regex that finds a bill number in free text, allowing common spellings."""
    letters = {"hr": r"H\.?\s?R", "s": r"S", "hjres": r"H\.?\s?J\.?\s?Res", "sjres": r"S\.?\s?J\.?\s?Res",
               "hconres": r"H\.?\s?Con\.?\s?Res", "sconres": r"S\.?\s?Con\.?\s?Res",
               "hres": r"H\.?\s?Res", "sres": r"S\.?\s?Res"}[btype]
    return re.compile(r"(?<![A-Za-z])" + letters + r"\.?\s?" + str(number) + r"(?!\d)", re.I)
