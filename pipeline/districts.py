"""Congressional district shapes, compactness scores and map outlines (U.S. Census Bureau).

Polsby-Popper score = 4π × area ÷ perimeter², measured in an equal-area projection centered on
each district, using the full shape including islands. 1 is a perfect circle.
"""
import io
import json
import logging
import math
import time
import warnings
import zipfile
from pathlib import Path

import pyproj
import shapefile
from shapely.geometry import MultiPolygon, Polygon, box, mapping, shape
from shapely import make_valid
from shapely.ops import polylabel, transform, unary_union

from .core import ROOT, out_path, write_json

warnings.filterwarnings("ignore")
log = logging.getLogger("solon")
CACHE = ROOT / ".cache"

STATES = {"01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE",
          "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA",
          "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
          "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM",
          "36": "NY", "37": "NC", "38": "ND", "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
          "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
          "54": "WV", "55": "WI", "56": "WY", "60": "AS", "66": "GU", "69": "MP", "72": "PR", "78": "VI"}
W, H, PAD = 480, 360, 14


def fetch_shapefile(http, url, local=None):
    """Return a pyshp Reader for a zipped shapefile (downloaded once and cached)."""
    if local:
        zpath = Path(local)
    else:
        CACHE.mkdir(exist_ok=True)
        zpath = CACHE / url.rsplit("/", 1)[-1]
        if not zpath.exists():
            zpath.write_bytes(http.get(url, timeout=600).content)
    z = zipfile.ZipFile(zpath)
    base = next(n[:-4] for n in z.namelist() if n.endswith(".shp"))
    return shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), dbf=io.BytesIO(z.read(base + ".dbf")),
                            shx=io.BytesIO(z.read(base + ".shx")))


def equal_area(g):
    c = g.centroid
    p = pyproj.Proj(proj="laea", lat_0=c.y, lon_0=c.x, ellps="WGS84")
    return transform(lambda x, y, z=None: p(x, y), g)


def polsby_popper(g):
    h = equal_area(g)
    return 4 * math.pi * h.area / h.length ** 2, h.area / 2_589_988.11


# ---------- SVG map outline in a 480 x 360 box ----------

def fitter(g):
    minx, miny, maxx, maxy = g.bounds
    k = math.cos(math.radians((miny + maxy) / 2))
    s = min((W - 2 * PAD) / ((maxx - minx) * k), (H - 2 * PAD) / (maxy - miny))
    ox = (W - (maxx - minx) * k * s) / 2
    oy = (H - (maxy - miny) * s) / 2
    return lambda x, y, z=None: (ox + (x - minx) * k * s, oy + (maxy - y) * s)


def _simplify_to(poly, n):
    tol = 0.1
    while True:
        p = poly.simplify(tol, preserve_topology=True)
        if len(p.exterior.coords) - 1 <= n:
            return p
        tol *= 1.15


def _pts(coords):
    out = []
    for x, y in coords:
        q = (round(x), round(y))
        if not out or out[-1] != q:
            out.append(q)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def _bridge(ring, islands):
    """One SVG polygon can't hold separate islands, so join each to the mainland with a zero-width line."""
    ring = list(ring)
    for isl in islands:
        _, i, j = min(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2, i, j)
                      for i, a in enumerate(ring) for j, b in enumerate(isl))
        ring = ring[:i + 1] + isl[j:] + isl[:j] + [isl[j], ring[i]] + ring[i + 1:]
    return ring


def svg_outline(g, max_points=72, max_islands=2, min_island_share=0.01):
    parts = sorted(g.geoms, key=lambda p: -p.area) if g.geom_type == "MultiPolygon" else [g]
    main = parts[0]
    islands = [p for p in parts[1:] if p.area >= min_island_share * main.area][:max_islands]
    keep = MultiPolygon([main] + islands) if islands else main
    fit = fitter(keep)
    pm = transform(fit, main)
    n_isl = 8 * len(islands)
    m = _pts(_simplify_to(pm, max(24, max_points - n_isl)).exterior.coords)
    isl = [_pts(_simplify_to(transform(fit, p), 7).exterior.coords) for p in islands]
    ring = _bridge(m, isl)
    outline = make_valid(unary_union([make_valid(Polygon(m))] + [make_valid(Polygon(i)) for i in isl if len(i) >= 3]))
    return " ".join(f"{x},{y}" for x, y in ring), fit, outline, bool(islands)


def label_point(g):
    if g.is_empty:
        return None
    if g.geom_type in ("MultiPolygon", "GeometryCollection"):
        g = max((p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"), key=lambda p: p.area, default=None)
        if g is None:
            return None
    p = polylabel(g, tolerance=0.5)
    return round(p.x), round(p.y)


def place_labels(areas, outline, char_w=7.6):
    """Move area labels so they sit inside their area and don't overlap each other."""
    from shapely.geometry import Point
    placed = []
    items = []
    for a in areas:
        poly = make_valid(Polygon([tuple(map(float, q.split(","))) for q in a["poly"].split()])).intersection(outline)
        if poly.geom_type != "Polygon":
            poly = max((q for q in getattr(poly, "geoms", []) if q.geom_type == "Polygon"), key=lambda q: q.area,
                       default=Polygon())
        items.append((poly.area, a, poly))
    for _, a, poly in sorted(items, key=lambda t: -t[0]):
        if poly.is_empty:
            continue
        minx, miny, maxx, maxy = poly.bounds
        best = None
        for i in range(31):
            for j in range(31):
                x, y = minx + (maxx - minx) * i / 30, miny + (maxy - miny) * j / 30
                if not poly.contains(Point(x, y)):
                    continue
                w = char_w * len(a["name"])
                b = box(x - w / 2, y - 12, x + w / 2, y + 4)
                over = sum(b.intersection(p).area for p in placed)
                oob = b.area - b.intersection(box(0, 0, W, H)).area
                score = over * 10 + oob * 10 - poly.boundary.distance(Point(x, y))
                if best is None or score < best[0]:
                    best = (score, x, y, b)
        if best:
            a["lx"], a["ly"] = round(best[1]), round(best[2])
            placed.append(best[3])


def county_areas(dist_geom, counties, fit, outline):
    """Counties that overlap a district, as polygons in the same 480x360 box."""
    rows = []
    for c in counties:
        try:
            if not c["geom"].intersects(dist_geom):
                continue
            inter = c["geom"].intersection(dist_geom)
            share = inter.area / c["geom"].area if c["geom"].area else 0
            if share < 0.002:
                continue
            g = make_valid(transform(fit, c["geom"])).intersection(box(-60, -60, W + 60, H + 60))
            g = make_valid(g.simplify(1.2, preserve_topology=True)).buffer(3, join_style=2)
            if g.geom_type == "MultiPolygon":
                g = max(g.geoms, key=lambda q: q.intersection(outline).area)
            if g.is_empty or g.geom_type != "Polygon" or g.intersection(outline).area < 4:
                continue
            rows.append({"fips": c["fips"], "county": c["name"], "share_of_county_area": round(share, 3),
                         "poly": " ".join(f"{x},{y}" for x, y in _pts(g.simplify(1.0).exterior.coords))})
        except Exception as e:  # one odd county shouldn't stop the district
            log.warning("County %s skipped: %s", c["name"], e)
    return rows


def build(cfg, http, members, local=None):
    """local: optional {"districts": zip path, "counties": zip path} for testing."""
    local = local or {}
    d = cfg["districts"]
    yr, cong = d["boundary_year"], cfg["congress"]
    rd = fetch_shapefile(http, d["district_file"].format(year=yr, congress=cong), local.get("districts"))
    rc = fetch_shapefile(http, d["county_file"].format(year=yr, congress=cong), local.get("counties"))
    holder = {m["district"]: m for m in members if m["chamber"] == "house"}
    counties_by_state = {}
    for sr in rc.iterShapeRecords():
        rec = sr.record.as_dict()
        counties_by_state.setdefault(rec["STATEFP"], []).append(
            {"fips": rec["GEOID"], "name": rec["NAME"], "geom": shape(sr.shape.__geo_interface__).buffer(0)})
    fp_field = next(f[0] for f in rd.fields[1:] if f[0].startswith("CD") and f[0].endswith("FP"))
    index, lookup = [], []
    for sr in rd.iterShapeRecords():
        rec = sr.record.as_dict()
        st = STATES.get(rec["STATEFP"])
        num = rec[fp_field]
        if num in ("98", "ZZ") or st is None:
            continue
        code = f"{st}-AL" if num == "00" else f"{st}-{num}"
        g = shape(sr.shape.__geo_interface__).buffer(0)
        pp, sqmi = polsby_popper(g)
        m = holder.get(code)
        try:
            svg, fit, outline, bridged = svg_outline(g)
            areas = county_areas(g, counties_by_state.get(rec["STATEFP"], []), fit, outline)
        except Exception as e:
            log.warning("Map outline failed for %s: %s", code, e)
            svg, bridged, areas = None, False, []
        detail = {
            "code": code, "state": st, "number": None if num == "00" else int(num), "geoid": rec["GEOID"],
            "congress": cong, "member": None if not m else {"bioguide": m["bioguide"], "name": m["name"],
                                                            "short_name": m.get("short_name"), "party": m["party"]},
            "polsby_popper": round(pp, 4), "square_miles": round(sqmi, 1),
            "svg": {"box": [W, H], "shape": svg, "islands_bridged": bridged, "counties": areas},
            "source": f"U.S. Census Bureau cartographic boundary file cb_{yr}_us_cd{cong}_500k and "
                      f"cb_{yr}_us_county_500k",
        }
        write_json(out_path(cfg, "districts", f"{code}.json"), detail)
        gs = g.simplify(0.002, preserve_topology=True)
        write_json(out_path(cfg, "districts", "shapes", f"{code}.geojson"),
                   {"type": "Feature", "properties": {"code": code}, "geometry": mapping(gs)})
        lookup.append({"type": "Feature", "properties": {"code": code},
                       "geometry": mapping(g.simplify(0.005, preserve_topology=True))})
        index.append({k: detail[k] for k in ("code", "state", "number", "member", "polsby_popper", "square_miles")})
    multi = {}
    for row in index:
        multi[row["state"]] = multi.get(row["state"], 0) + 1
    ranked = sorted((r for r in index if multi[r["state"]] > 1), key=lambda r: r["polsby_popper"])
    for i, r in enumerate(ranked, 1):
        r["rank_least_compact"] = i
    for party in ("Democrat", "Republican"):
        held = [r for r in ranked if r["member"] and r["member"]["party"] == party]
        for i, r in enumerate(held, 1):
            r["rank_least_compact_in_party"] = i
    index.sort(key=lambda r: r["code"])
    write_json(out_path(cfg, "districts", "index.json"), {
        "congress": cong, "boundary_file": f"cb_{yr}_us_cd{cong}_500k", "count": len(index),
        "ranked_districts": len(ranked),
        "note": "Ranks cover districts in states with more than one House seat. Rank 1 is the least compact.",
        "districts": index, "updated": time.strftime("%Y-%m-%d")})
    # One small file the app can use to find a district from a map point.
    p = out_path(cfg, "districts", "lookup.geojson")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": lookup}, f, separators=(",", ":"))
    log.info("Districts: %d written, %d ranked", len(index), len(ranked))
    return index
