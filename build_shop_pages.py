"""Builds a product page on analyticollc.com for every active Etsy listing, plus the ChatGPT feed.

What it writes (inside this website folder):
    shop/index.html                      every category, with counts
    shop/<category>/index.html, page/N/  product grids, 60 per page
    shop/<slug>-<listing id>/index.html  one page per listing, with a Buy On Etsy button
    feeds/chatgpt_feed.csv.gz            the ChatGPT product feed, pointing at the pages above
    sitemap.xml, robots.txt              so Google finds every page

Runs every night on GitHub (see .github/workflows/build-shop.yml) with the same two repo secrets
as the section refresh, ETSY_KEYSTRING and ETSY_SHARED_SECRET. Also runs on your PC:
    python build_shop_pages.py [path\\to\\etsy_tokens.json]
Read-only: about two Etsy calls per 100 listings, no sign-in token, nothing on Etsy changes.
The whole shop folder is rebuilt each run, so listings that are no longer active drop off.
"""
import csv
import datetime
import gzip
import html
import io
import json
import os
import re
import shutil
import sys
import time

import requests

SHOP_ID = "45435667"
API = "https://api.etsy.com/v3/application"
SITE = "https://analyticollc.com"
BRAND = "Analytico LLC"
PER_PAGE = 60
FEED_FIELDS = ["item_id", "title", "description", "url", "brand", "seller_name", "image_url",
               "additional_image_urls", "availability", "price", "condition", "target_countries",
               "store_country"]
ETSY_NOTE = ("The term 'Etsy' is a trademark of Etsy, Inc. This site uses the Etsy API but is not "
             "endorsed or certified by Etsy, Inc.")


# ---------------------------------------------------------------- Etsy
def _get(hdr, path, **params):
    for attempt in range(4):
        r = requests.get(f"{API}{path}", headers=hdr, params=params, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        time.sleep(0.15)
        return r.json()
    r.raise_for_status()


def fetch(hdr, log=print):
    sections = {str(s["shop_section_id"]): s.get("title", "").strip()
                for s in _get(hdr, f"/shops/{SHOP_ID}/sections").get("results", [])}
    out, offset = [], 0
    while True:
        d = _get(hdr, f"/shops/{SHOP_ID}/listings/active", limit=100, offset=offset)
        res = d.get("results") or []
        # the shop list leaves photos out without a sign-in, so ask for these 100 again with photos
        ids = ",".join(str(li["listing_id"]) for li in res)
        withimg = {str(li["listing_id"]): li for li in
                   (_get(hdr, "/listings/batch", listing_ids=ids, includes="Images").get("results") or [])} if ids else {}
        for li in res:
            li["images"] = (withimg.get(str(li["listing_id"])) or {}).get("images") or li.get("images") or []
        out += res
        offset += len(res)
        log(f"  {offset} of {d.get('count', '?')} listings")
        if len(res) < 100:
            break
    return out, sections


# ---------------------------------------------------------------- text helpers
def esc(s):
    return html.escape(str(s or ""), quote=True)


def plain(s, limit=None):
    s = html.unescape(str(s or ""))
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    if limit and len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:") + "..."
    return s


def slug(s, n=60):
    s = re.sub(r"[^a-z0-9]+", "-", plain(s).lower()).strip("-")
    return (s[:n].rsplit("-", 1)[0] if len(s) > n else s) or "item"


def price_of(li):
    p = li.get("price") or {}
    try:
        amt = float(p.get("amount")) / float(p.get("divisor") or 100)
    except (TypeError, ValueError):
        return None, ""
    return (amt, str(p.get("currency_code") or "USD").upper()) if amt > 0 else (None, "")


def images_of(li):
    imgs = sorted(li.get("images") or [], key=lambda i: i.get("rank") if i.get("rank") is not None else 99)
    return [(i.get("url_570xN") or i.get("url_fullxfull"), i.get("url_fullxfull") or i.get("url_570xN"),
             i.get("alt_text") or "") for i in imgs if i.get("url_570xN") or i.get("url_fullxfull")]


def short_title(t):
    """The part before the first separator reads best as a page heading."""
    first = re.split(r"\s[|,]\s|\s[-:]\s", plain(t))[0].strip()
    return first if len(first) >= 12 else plain(t)


# ---------------------------------------------------------------- page shell
CSS = """
:root { --green: #00B050; --red: #C00000; --ink: #222; --soft: #F4F7F5; --muted: #666; }
* { box-sizing: border-box; }
body { margin: 0; font-family: Arial, Helvetica, sans-serif; color: var(--ink); line-height: 1.6; background: #fff; }
.wrap { max-width: 980px; margin: 0 auto; padding: 0 18px; }
header { border-bottom: 4px solid var(--green); }
header .wrap { display: flex; justify-content: space-between; align-items: center; padding-top: 14px; padding-bottom: 14px; flex-wrap: wrap; gap: 10px; }
.brand { font-size: 1.5rem; font-weight: 700; color: var(--ink); text-decoration: none; }
.brand span { color: var(--green); }
nav a { color: var(--ink); text-decoration: none; margin-left: 18px; font-weight: 600; }
nav a:hover { color: var(--green); }
main { padding: 26px 0 48px; }
.crumbs { font-size: .9rem; color: var(--muted); margin-bottom: 14px; }
.crumbs a { color: var(--muted); }
h1 { font-size: 1.6rem; margin: 0 0 10px; line-height: 1.3; }
h1 .bar, h2 .bar { display: inline-block; width: 34px; height: 4px; background: var(--red); vertical-align: middle; margin-right: 10px; }
.btn { display: inline-block; background: var(--green); color: #fff; padding: 13px 26px; border-radius: 6px; text-decoration: none; font-weight: 700; }
.btn:hover { background: #009443; }
.prod { display: grid; grid-template-columns: 1.1fr 1fr; gap: 28px; align-items: start; }
.gal .main { width: 100%; aspect-ratio: 1 / 1; object-fit: contain; background: var(--soft); border-radius: 8px; display: block; }
.thumbs { display: grid; grid-template-columns: repeat(auto-fill, minmax(64px, 1fr)); gap: 6px; margin-top: 8px; }
.thumbs img { width: 100%; aspect-ratio: 1 / 1; object-fit: cover; border-radius: 4px; cursor: pointer; border: 2px solid transparent; background: var(--soft); }
.thumbs img:hover { border-color: var(--green); }
.price { font-size: 1.5rem; font-weight: 700; margin: 6px 0 14px; }
.price small { font-size: .9rem; color: var(--muted); font-weight: 400; }
.desc { white-space: pre-line; color: #333; margin-top: 22px; font-size: .97rem; }
.note { color: var(--muted); font-size: .88rem; margin-top: 10px; }
.secs { display: grid; grid-template-columns: repeat(auto-fill, minmax(165px, 1fr)); gap: 14px; margin-top: 16px; }
.sec { display: flex; flex-direction: column; background: #fff; border: 1px solid #e3e8e5; border-radius: 8px; overflow: hidden; color: var(--ink); text-decoration: none; }
.sec:hover { border-color: var(--green); box-shadow: 0 3px 10px rgba(0,0,0,.08); }
.sec img { width: 100%; aspect-ratio: 1 / 1; object-fit: cover; background: var(--soft); display: block; }
.sec b { padding: 10px 12px 0; font-size: .92rem; font-weight: 600; line-height: 1.35; }
.sec:hover b { color: var(--green); }
.sec span { padding: 4px 12px 11px; color: var(--muted); font-size: .88rem; }
.pager { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 24px; }
.pager a, .pager b { padding: 6px 12px; border: 1px solid #ddd; border-radius: 6px; text-decoration: none; color: var(--ink); }
.pager b { background: var(--green); color: #fff; border-color: var(--green); }
footer { border-top: 1px solid #ddd; padding: 20px 0; color: var(--muted); font-size: .9rem; }
footer .wrap { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
footer a { color: var(--muted); }
footer .etsy { flex-basis: 100%; font-size: .8rem; }
@media (max-width: 700px) { .prod { grid-template-columns: 1fr; } nav a { margin-left: 12px; } h1 { font-size: 1.35rem; } }
"""


def shell(title, desc, canon, body, extra_head=""):
    year = datetime.date.today().year
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(plain(desc, 155))}">
<link rel="canonical" href="{esc(canon)}">
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" type="image/png" href="/favicon-32.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="stylesheet" href="/shop/shop.css">
{extra_head}</head>
<body>
<header>
  <div class="wrap">
    <a class="brand" href="/">Analytico <span>LLC</span></a>
    <nav>
      <a href="/shop/">Shop</a>
      <a href="/about">About</a>
      <a href="/#contact">Contact</a>
      <a href="/privacy">Privacy</a>
    </nav>
  </div>
</header>
<main><div class="wrap">
{body}
</div></main>
<footer>
  <div class="wrap">
    <span>&copy; {year} Analytico LLC</span>
    <span><a href="/about">About</a> &middot; <a href="/privacy">Privacy Policy</a> &middot; <a href="https://analyticollc.etsy.com">Etsy Shop</a></span>
    <span class="etsy">{esc(ETSY_NOTE)}</span>
  </div>
</footer>
</body>
</html>
"""


def card(p):
    return (f'<a class="sec" href="{p["path"]}"><img src="{esc(p["thumb"])}" alt="{esc(p["short"])}" '
            f'loading="lazy"><b>{esc(p["short"])}</b><span>${p["amount"]:.2f}</span></a>')


# ---------------------------------------------------------------- build
def build(listings, sections, root, log=print):
    shop_dir = os.path.join(root, "shop")
    if os.path.isdir(shop_dir):
        shutil.rmtree(shop_dir)
    os.makedirs(shop_dir)
    with open(os.path.join(shop_dir, "shop.css"), "w", encoding="utf-8") as fh:
        fh.write(CSS.strip() + "\n")

    prods, skipped, urls = [], [], []
    for li in listings:
        lid = str(li.get("listing_id") or "")
        imgs = images_of(li)
        amount, cur = price_of(li)
        title = plain(li.get("title"), 150)
        if not (lid and title and imgs and amount):
            skipped.append(lid)
            continue
        sec_name = sections.get(str(li.get("shop_section_id") or ""), "") or "More Designs"
        path = f"/shop/{slug(short_title(title))}-{lid}/"
        prods.append({"id": lid, "title": title, "short": short_title(title), "imgs": imgs,
                      "thumb": imgs[0][0], "amount": amount, "cur": cur, "path": path, "section": sec_name,
                      "desc": plain(li.get("description")), "qty": int(li.get("quantity") or 0),
                      "etsy": f"https://www.etsy.com/listing/{lid}", "favs": li.get("num_favorers") or 0})

    sec_slug = {}
    for name in sorted({p["section"] for p in prods}):
        s = slug(name, 40)
        while s in sec_slug.values():
            s += "-x"
        sec_slug[name] = s

    # one page per product
    for p in prods:
        gal = (f'<img class="main" id="m" src="{esc(p["imgs"][0][1])}" alt="{esc(p["imgs"][0][2] or p["short"])}">'
               + ('<div class="thumbs">' + "".join(
                   f'<img src="{esc(a)}" data-big="{esc(b)}" alt="{esc(alt or p["short"])}" loading="lazy" '
                   f'onclick="document.getElementById(\'m\').src=this.dataset.big">' for a, b, alt in p["imgs"][:10])
                  + "</div>" if len(p["imgs"]) > 1 else ""))
        ld = {"@context": "https://schema.org", "@type": "Product", "name": p["title"], "sku": p["id"],
              "image": [b for _, b, _ in p["imgs"][:6]], "description": plain(p["desc"], 900),
              "brand": {"@type": "Brand", "name": BRAND},
              "offers": {"@type": "Offer", "url": p["etsy"], "priceCurrency": p["cur"],
                         "price": f'{p["amount"]:.2f}', "itemCondition": "https://schema.org/NewCondition",
                         "availability": "https://schema.org/" + ("InStock" if p["qty"] > 0 else "OutOfStock"),
                         "seller": {"@type": "Organization", "name": BRAND}}}
        ld_txt = json.dumps(ld, ensure_ascii=False).replace("</", "<\\/")
        body = (f'<div class="crumbs"><a href="/shop/">Shop</a> &rsaquo; '
                f'<a href="/shop/{sec_slug[p["section"]]}/">{esc(p["section"])}</a></div>'
                f'<div class="prod"><div class="gal">{gal}</div><div>'
                f'<h1>{esc(p["title"])}</h1>'
                f'<div class="price">${p["amount"]:.2f} <small>{esc(p["cur"])}, starting price</small></div>'
                f'<a class="btn" href="{p["etsy"]}" rel="nofollow">Buy On Etsy</a>'
                f'<div class="note">Choose your size and color on Etsy. Every item is printed to order and '
                f'checkout is handled securely by Etsy.</div>'
                f'<div class="desc">{esc(p["desc"])}</div></div></div>')
        page = shell(f'{p["short"]} | {BRAND}', p["desc"] or p["title"], SITE + p["path"], body,
                     f'<meta property="og:title" content="{esc(p["short"])}">\n'
                     f'<meta property="og:image" content="{esc(p["imgs"][0][1])}">\n'
                     f'<meta property="og:url" content="{esc(SITE + p["path"])}">\n'
                     '<script type="application/ld+json">' + ld_txt + '</script>\n')
        d = os.path.join(root, p["path"].strip("/").replace("/", os.sep))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as fh:
            fh.write(page)
        urls.append(SITE + p["path"])

    # category pages, most-favorited first
    by_sec = {}
    for p in prods:
        by_sec.setdefault(p["section"], []).append(p)
    tiles = []
    for name, items in sorted(by_sec.items(), key=lambda kv: -len(kv[1])):
        items.sort(key=lambda p: -p["favs"])
        s = sec_slug[name]
        pages = max(1, -(-len(items) // PER_PAGE))
        for n in range(1, pages + 1):
            rel = f"/shop/{s}/" + (f"page/{n}/" if n > 1 else "")
            chunk = items[(n - 1) * PER_PAGE:n * PER_PAGE]
            pager = ""
            if pages > 1:
                pager = '<div class="pager">' + "".join(
                    f"<b>{k}</b>" if k == n else f'<a href="/shop/{s}/' + (f"page/{k}/" if k > 1 else "") + f'">{k}</a>'
                    for k in range(1, pages + 1)) + "</div>"
            body = (f'<div class="crumbs"><a href="/shop/">Shop</a> &rsaquo; {esc(name)}</div>'
                    f'<h1><span class="bar"></span>{esc(name)}</h1><p>{len(items):,} designs'
                    + (f", page {n} of {pages}" if pages > 1 else "") + "</p>"
                    f'<div class="secs">{"".join(card(p) for p in chunk)}</div>{pager}')
            d = os.path.join(root, rel.strip("/").replace("/", os.sep))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as fh:
                fh.write(shell(f"{name}" + (f" (Page {n})" if n > 1 else "") + f" | {BRAND}",
                               f"Shop {len(items):,} {name} designs from {BRAND}, printed to order.",
                               SITE + rel, body))
            urls.append(SITE + rel)
        tiles.append(f'<a class="sec" href="/shop/{s}/"><img src="{esc(items[0]["thumb"])}" alt="{esc(name)}" '
                     f'loading="lazy"><b>{esc(name)}</b><span>{len(items):,} designs</span></a>')

    body = (f'<h1><span class="bar"></span>Shop All Designs</h1><p>{len(prods):,} designs, printed to order. '
            f'Pick a category below.</p><div class="secs">{"".join(tiles)}</div>')
    with open(os.path.join(shop_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(shell(f"Shop All Designs | {BRAND}", f"Browse {len(prods):,} designs from {BRAND}.",
                       SITE + "/shop/", body))
    urls.insert(0, SITE + "/shop/")

    # sitemap and robots
    with open(os.path.join(root, "sitemap.xml"), "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                 + "".join(f"  <url><loc>{esc(u)}</loc></url>\n"
                           for u in [SITE + "/", SITE + "/about", SITE + "/privacy"] + urls)
                 + "</urlset>\n")
    with open(os.path.join(root, "robots.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"User-agent: *\nAllow: /\n\nSitemap: {SITE}/sitemap.xml\n")

    # ChatGPT feed
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FEED_FIELDS, lineterminator="\n")
    w.writeheader()
    for p in prods:
        w.writerow({"item_id": p["id"], "title": p["title"], "description": plain(p["desc"], 4800),
                    "url": SITE + p["path"], "brand": BRAND, "seller_name": BRAND,
                    "image_url": p["imgs"][0][1],
                    "additional_image_urls": json.dumps([b for _, b, _ in p["imgs"][1:9]]) if len(p["imgs"]) > 1 else "",
                    "availability": "in_stock" if p["qty"] > 0 else "out_of_stock",
                    "price": f'{p["amount"]:.2f} {p["cur"]}', "condition": "new",
                    "target_countries": json.dumps(["US"]), "store_country": "US"})
    os.makedirs(os.path.join(root, "feeds"), exist_ok=True)
    with gzip.GzipFile(os.path.join(root, "feeds", "chatgpt_feed.csv.gz"), "wb", mtime=0) as fh:
        fh.write(buf.getvalue().encode("utf-8"))

    log(f"Built {len(prods):,} product pages in {len(by_sec)} categories"
        + (f", left out {len(skipped)} (no photo or price): {', '.join(skipped[:20])}" if skipped else ""))
    return {"pages": len(prods), "categories": len(by_sec), "skipped": skipped}


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    key, secret = os.environ.get("ETSY_KEYSTRING"), os.environ.get("ETSY_SHARED_SECRET")
    if not (key and secret):
        cands = sys.argv[1:] + [r"D:\Google Drive\My APIs\etsy_tokens.json", os.path.join(here, "etsy_tokens.json")]
        tok_file = next((c for c in cands if os.path.exists(c)), None)
        if not tok_file:
            sys.exit("No ETSY_KEYSTRING / ETSY_SHARED_SECRET and no etsy_tokens.json found.")
        with open(tok_file, encoding="utf-8") as fh:
            tok = json.load(fh)
        key, secret = tok["keystring"], tok["shared_secret"]
    hdr = {"x-api-key": f"{key}:{secret}"}
    print("Reading active listings from Etsy...")
    listings, sections = fetch(hdr)
    if len(listings) < 100:
        sys.exit(f"Only {len(listings)} listings came back, so nothing was changed (safety stop).")
    good = sum(1 for li in listings if li.get("images") and price_of(li)[0])
    if good < len(listings) * 0.5:
        sys.exit(f"Only {good} of {len(listings)} listings had a photo and price, so nothing was changed "
                 "(safety stop).")
    build(listings, sections, here)


if __name__ == "__main__":
    main()
