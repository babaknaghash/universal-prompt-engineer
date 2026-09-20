#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv, json, math, os, re, statistics, time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

CATEGORY_ID = 11
BASE = "https://api.digikala.com"
OUT = "digikala_mobile_pack"
DETAIL_TOP_N = 200
SECONDARY_TOP_N = 100
PAGE_DELAY = 0.18
DETAIL_DELAY = 0.15

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.7",
}

PRICE_BANDS = [
    ("زیر ۲۰ میلیون", None, 20_000_000),
    ("۲۰ تا ۳۰ میلیون", 20_000_000, 30_000_000),
    ("۳۰ تا ۵۰ میلیون", 30_000_000, 50_000_000),
    ("۵۰ تا ۷۵ میلیون", 50_000_000, 75_000_000),
    ("۷۵ تا ۱۰۰ میلیون", 75_000_000, 100_000_000),
    ("۱۰۰ تا ۱۵۰ میلیون", 100_000_000, 150_000_000),
    ("۱۵۰ میلیون و بیشتر", 150_000_000, None),
]

def session():
    s = requests.Session()
    retry = Retry(
        total=5, connect=5, read=5, status=5,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20))
    s.headers.update(HEADERS)
    return s

S = session()

def get_json(url, params=None, timeout=35):
    r = S.get(url, params=params, timeout=timeout)
    print("GET", r.status_code, r.url)
    r.raise_for_status()
    return r.json()

def search(page=1, sort=1, pmin=None, pmax=None):
    params = {"categories[]": CATEGORY_ID, "page": page, "sort": sort}
    if pmin is not None:
        params["price[min]"] = int(pmin * 10)
    if pmax is not None:
        params["price[max]"] = int(pmax * 10)
    return get_json(f"{BASE}/v1/search/", params=params)

def price_dict(p):
    dv = p.get("default_variant") or {}
    return dv, (dv.get("price") or {})

def norm_product(p):
    dv, pr = price_dict(p)
    rating = p.get("rating") or {}
    brand = p.get("brand") or {}
    dl = p.get("data_layer") or {}
    url = p.get("url") or {}
    seller = dv.get("seller") or {}
    selling = pr.get("selling_price")
    rrp = pr.get("rrp_price")
    if not isinstance(selling, (int, float)):
        selling = None
    if not isinstance(rrp, (int, float)):
        rrp = None
    return {
        "dkp": p.get("id"),
        "title_fa": p.get("title_fa"),
        "title_en": p.get("title_en"),
        "status": p.get("status"),
        "brand": brand.get("title_fa") or dl.get("brand") or "",
        "brand_code": brand.get("code") or "",
        "category": (p.get("category") or {}).get("title_fa") or dl.get("category") or "",
        "selling_price_toman": selling / 10 if selling else None,
        "rrp_price_toman": rrp / 10 if rrp else None,
        "discount_percent": pr.get("discount_percent") or 0,
        "rating_rate_raw": rating.get("rate") or 0,
        "rating_count": rating.get("count") or 0,
        "default_variant_id": dv.get("id"),
        "default_seller": seller.get("title") or seller.get("code") or "",
        "url": ("https://www.digikala.com" + url.get("uri")) if isinstance(url, dict) and url.get("uri") else "",
    }

def pager_info(payload):
    data = payload.get("data") or {}
    pg = data.get("pager") or {}
    return {
        "current_page": pg.get("current_page"),
        "total_pages": pg.get("total_pages"),
        "total_items": pg.get("total_items"),
    }

def products_from(payload):
    return (payload.get("data") or {}).get("products") or []

def crawl(sort=1, max_products=None, pmin=None, pmax=None, tag="crawl"):
    out = []
    seen = set()
    page = 1
    total_pages = None
    total_items = None
    while True:
        payload = search(page=page, sort=sort, pmin=pmin, pmax=pmax)
        if page == 1:
            os.makedirs(OUT, exist_ok=True)
            with open(os.path.join(OUT, f"raw_{tag}_page1.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        pg = pager_info(payload)
        total_pages = pg.get("total_pages") or total_pages
        total_items = pg.get("total_items") or total_items
        ps = products_from(payload)
        if not ps:
            break
        for p in ps:
            row = norm_product(p)
            pid = row["dkp"]
            if not pid or pid in seen:
                continue
            seen.add(pid)
            if row["status"] != "marketable":
                continue
            out.append(row)
            if max_products and len(out) >= max_products:
                return out, {"total_pages": total_pages, "total_items": total_items, "pages_fetched": page}
        if total_pages and page >= total_pages:
            break
        if max_products and len(out) >= max_products:
            break
        if not total_pages and len(ps) < 20:
            break
        page += 1
        if page > 600:
            raise RuntimeError("Unexpectedly exceeded 600 pages")
        if page % 20 == 0:
            print(tag, "progress page", page, "rows", len(out), "total_pages", total_pages, "total_items", total_items)
        time.sleep(PAGE_DELAY)
    return out, {"total_pages": total_pages, "total_items": total_items, "pages_fetched": page}

def percentile(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * p
    lo = int(math.floor(pos)); hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)

def describe(rows, key="selling_price_toman"):
    xs = [float(r[key]) for r in rows if isinstance(r.get(key), (int,float)) and r[key] > 0]
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "mean": statistics.mean(xs),
        "median": statistics.median(xs),
        "p25": percentile(xs, .25),
        "p75": percentile(xs, .75),
        "min": min(xs),
        "max": max(xs),
    }

def write_csv(name, rows):
    path = os.path.join(OUT, name)
    if not rows:
        open(path, "w", encoding="utf-8").close()
        return
    fields = []
    seen = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k); fields.append(k)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

def rank_rows(rows, rank_name):
    out = []
    for i, r in enumerate(rows, 1):
        rr = dict(r)
        rr[rank_name] = i
        out.append(rr)
    return out

def detail(pid):
    payload = get_json(f"{BASE}/v2/product/{pid}/")
    return ((payload.get("data") or {}).get("product") or {})

def flatten_specs(product):
    d = {}
    for group in product.get("specifications") or []:
        for a in group.get("attributes") or []:
            title = str(a.get("title") or "").strip()
            vals = a.get("values") or []
            if title and vals:
                d[title] = " | ".join(str(v) for v in vals)
    return d

def pick_spec(specs, patterns):
    for title, val in specs.items():
        t = title.replace("ي","ی").replace("ك","ک")
        for pat in patterns:
            if pat in t:
                return val
    return ""

def parse_capacity_from_text(text):
    if not text: return ""
    t = str(text).replace("گیگابایت"," GB ").replace("ترابایت"," TB ")
    matches = re.findall(r"(?<!\d)(64|128|256|512|1024)\s*(?:GB|گیگ)?", t, flags=re.I)
    if matches:
        return matches[-1] + " GB"
    tb = re.findall(r"(?<!\d)(1|2)\s*(?:TB|ترا)", t, flags=re.I)
    if tb:
        return str(int(tb[-1]) * 1024) + " GB"
    return ""

def enrich_top(rows):
    out = []
    for idx, r in enumerate(rows[:DETAIL_TOP_N], 1):
        rr = dict(r)
        try:
            p = detail(r["dkp"])
            specs = flatten_specs(p)
            rr["storage"] = pick_spec(specs, ["حافظه داخلی", "ظرفیت حافظه داخلی"]) or parse_capacity_from_text(rr.get("title_fa",""))
            rr["ram"] = pick_spec(specs, ["مقدار RAM", "حافظه رم", "ظرفیت حافظه رم"])
            rr["network"] = pick_spec(specs, ["شبکه های ارتباطی", "شبکه‌های ارتباطی", "فناوری شبکه"])
            rr["chipset"] = pick_spec(specs, ["تراشه", "پردازنده مرکزی"])
            rr["screen_size"] = pick_spec(specs, ["اندازه صفحه نمایش", "اندازه صفحه‌نمایش"])
            rr["battery"] = pick_spec(specs, ["ظرفیت باتری"])
            rr["specs_json"] = json.dumps(specs, ensure_ascii=False)
            rr["detail_status"] = p.get("status")
        except Exception as e:
            rr["detail_error"] = repr(e)
        out.append(rr)
        if idx % 25 == 0:
            print("details", idx, "/", min(len(rows), DETAIL_TOP_N))
        time.sleep(DETAIL_DELAY)
    return out

def main():
    os.makedirs(OUT, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()

    # Full current catalog and full best-selling ranking
    catalog, catalog_meta = crawl(sort=1, tag="catalog")
    best, best_meta = crawl(sort=5, tag="best_selling")
    best = rank_rows(best, "best_selling_rank")

    # Top 100 secondary rankings
    viewed, viewed_meta = crawl(sort=6, max_products=SECONDARY_TOP_N, tag="most_viewed")
    viewed = rank_rows(viewed, "most_viewed_rank")
    rated, rated_meta = crawl(sort=7, max_products=SECONDARY_TOP_N, tag="highest_rated")
    rated = rank_rows(rated, "highest_rated_rank")

    write_csv("catalog.csv", catalog)
    write_csv("ranking_best_selling.csv", best)
    write_csv("ranking_most_viewed_top100.csv", viewed)
    write_csv("ranking_highest_rated_top100.csv", rated)

    # Enrich top best sellers with detailed specs for future cross-market matching.
    enriched = enrich_top(best)
    write_csv("bestsellers_top200_enriched.csv", enriched)

    # Price bands: full catalog distribution + top 20 best-selling inside each band.
    band_rows = []
    band_best_rows = []
    total_catalog = len(catalog)
    for label, lo, hi in PRICE_BANDS:
        members = [r for r in catalog if isinstance(r.get("selling_price_toman"), (int,float))
                   and (lo is None or r["selling_price_toman"] >= lo)
                   and (hi is None or r["selling_price_toman"] < hi)]
        ds = describe(members)
        try:
            band_best, band_meta = crawl(sort=5, max_products=20, pmin=lo, pmax=hi, tag="band_" + re.sub(r"\W+","_",label))
        except Exception as e:
            print("Filtered band crawl failed, using global best ranking", label, repr(e))
            band_best = [r for r in best
                         if isinstance(r.get("selling_price_toman"), (int,float))
                         and (lo is None or r["selling_price_toman"] >= lo)
                         and (hi is None or r["selling_price_toman"] < hi)][:20]
            band_meta = {"fallback": True}

        top = band_best[0] if band_best else {}
        band_rows.append({
            "price_band": label,
            "min_toman": lo,
            "max_toman_exclusive": hi,
            "product_count": len(members),
            "share_of_catalog_pct": (100 * len(members)/total_catalog) if total_catalog else None,
            "mean_price_toman": ds.get("mean"),
            "median_price_toman": ds.get("median"),
            "p25_price_toman": ds.get("p25"),
            "p75_price_toman": ds.get("p75"),
            "bestseller_dkp": top.get("dkp"),
            "bestseller_title": top.get("title_fa"),
            "bestseller_brand": top.get("brand"),
            "bestseller_price_toman": top.get("selling_price_toman"),
        })
        for i, r in enumerate(band_best, 1):
            rr = dict(r); rr["price_band"] = label; rr["band_rank"] = i
            band_best_rows.append(rr)

    write_csv("price_band_summary.csv", band_rows)
    write_csv("price_band_bestsellers_top20.csv", band_best_rows)

    # Brand summary from catalog + global bestseller ranking.
    best_by_brand = defaultdict(list)
    for r in best:
        best_by_brand[r.get("brand") or "نامشخص"].append(r)
    catalog_by_brand = defaultdict(list)
    for r in catalog:
        catalog_by_brand[r.get("brand") or "نامشخص"].append(r)

    brand_rows = []
    brand_best_rows = []
    top20_ids = {r["dkp"] for r in best[:20]}
    top50_ids = {r["dkp"] for r in best[:50]}
    top100_ids = {r["dkp"] for r in best[:100]}
    for brand, rs in sorted(catalog_by_brand.items(), key=lambda kv: len(kv[1]), reverse=True):
        ds = describe(rs)
        brank = best_by_brand.get(brand, [])
        top = brank[0] if brank else {}
        ids = {r["dkp"] for r in rs}
        brand_rows.append({
            "brand": brand,
            "catalog_product_count": len(rs),
            "catalog_share_pct": 100*len(rs)/total_catalog if total_catalog else None,
            "mean_price_toman": ds.get("mean"),
            "median_price_toman": ds.get("median"),
            "p25_price_toman": ds.get("p25"),
            "p75_price_toman": ds.get("p75"),
            "min_price_toman": ds.get("min"),
            "max_price_toman": ds.get("max"),
            "best_global_rank": top.get("best_selling_rank"),
            "bestseller_dkp": top.get("dkp"),
            "bestseller_title": top.get("title_fa"),
            "bestseller_price_toman": top.get("selling_price_toman"),
            "models_in_top20": len(ids & top20_ids),
            "models_in_top50": len(ids & top50_ids),
            "models_in_top100": len(ids & top100_ids),
        })
        for r in brank[:10]:
            rr = dict(r); rr["brand_group"] = brand
            brand_best_rows.append(rr)

    write_csv("brand_summary.csv", brand_rows)
    write_csv("brand_bestsellers_top10.csv", brand_best_rows)

    # Ranking overlap / attention vs sales.
    bs20 = {r["dkp"] for r in best[:20]}
    vw20 = {r["dkp"] for r in viewed[:20]}
    rt20 = {r["dkp"] for r in rated[:20]}
    overlap_rows = [{
        "metric": "best_selling_vs_most_viewed_top20_overlap",
        "count": len(bs20 & vw20), "denominator": 20,
        "pct": 100*len(bs20 & vw20)/20
    },{
        "metric": "best_selling_vs_highest_rated_top20_overlap",
        "count": len(bs20 & rt20), "denominator": 20,
        "pct": 100*len(bs20 & rt20)/20
    },{
        "metric": "triple_overlap_top20",
        "count": len(bs20 & vw20 & rt20), "denominator": 20,
        "pct": 100*len(bs20 & vw20 & rt20)/20
    }]
    write_csv("ranking_overlap.csv", overlap_rows)

    # Discount statistics
    priced = [r for r in catalog if r.get("selling_price_toman")]
    discounted = [r for r in priced if (r.get("discount_percent") or 0) > 0]

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "started_at_utc": started,
        "source": "api.digikala.com",
        "scope": "Current marketable mobile-phone products on Digikala, category_id=11",
        "price_definition": "default_variant.price.selling_price converted from rial to toman",
        "catalog_meta": catalog_meta,
        "best_selling_meta": best_meta,
        "most_viewed_meta": viewed_meta,
        "highest_rated_meta": rated_meta,
        "catalog_product_count": len(catalog),
        "catalog_price_stats_toman": describe(catalog),
        "top20_best_selling_price_stats_toman": describe(best[:20]),
        "top50_best_selling_price_stats_toman": describe(best[:50]),
        "top100_best_selling_price_stats_toman": describe(best[:100]),
        "discounted_product_count": len(discounted),
        "discounted_product_share_pct": 100*len(discounted)/len(priced) if priced else None,
        "discount_percent_stats_among_discounted": describe(discounted, key="discount_percent"),
        "top20_best_selling": best[:20],
        "price_bands": band_rows,
        "ranking_overlap_top20": overlap_rows,
        "notes": [
            "Best Selling is the ordering returned by Digikala sort=5; unit sales are not exposed.",
            "Catalog share by brand means share of currently marketable DKP listings, not sales share.",
            "Brand bestseller lists are derived from Digikala's global mobile Best Selling order.",
            "RAM/storage enrichment is performed only for the top 200 best-selling products."
        ]
    }
    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    with open(os.path.join(OUT, "README.txt"), "w", encoding="utf-8") as f:
        f.write("Digikala Mobile Data Pack\n")
        f.write("Generated: " + summary["generated_at_utc"] + "\n")
        f.write("Category ID: 11 (mobile-phone)\n")
        f.write("Price = default variant selling price in toman.\n")
        f.write("Best Selling = Digikala sort=5 ranking; no unit-sales counts are available.\n")
        f.write("Brand share = share of current catalog DKPs, not market/sales share.\n")

    print(json.dumps({
        "catalog": len(catalog),
        "best": len(best),
        "viewed": len(viewed),
        "rated": len(rated),
        "summary": summary["catalog_price_stats_toman"],
        "top20": summary["top20_best_selling_price_stats_toman"],
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
