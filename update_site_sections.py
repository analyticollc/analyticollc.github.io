"""Refreshes the homepage Shop By Category tiles from the real Etsy shop sections.

For every tile it sets the section link and listing count, and picks one photo: the
listing in that section with the most Etsy favorites.

Runs weekly on GitHub (see .github/workflows/update-sections.yml) using two repo secrets,
ETSY_KEYSTRING and ETSY_SHARED_SECRET. Also runs on your PC:
    python update_site_sections.py [path\\to\\etsy_tokens.json]
Read-only: about one Etsy call per 100 listings, no sign-in token needed, nothing on Etsy changes.
The Listadum app ships this same file as lib/site_sections.py, so keep the two identical.
"""
import datetime
import html as htmlmod
import json
import os
import re
import sys
import time

import requests

SHOP_ID = "45435667"
API = "https://api.etsy.com/v3/application"
PLACEHOLDER = "/apple-touch-icon.png"      # shown until a section has a photo
MAX_PAGES = 40                             # up to 4,000 listings scanned per section
TILE_RE = re.compile(r'<a class="sec" data-section="([^"]+)"[^>]*>.*?</a>', re.S)


def headers(key, secret):
    return {"x-api-key": f"{key}:{secret}"}


def _get(hdr, path, **params):
    for attempt in range(3):
        r = requests.get(f"{API}{path}", headers=hdr, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        time.sleep(0.15)                   # stay well under Etsy's per-second limit
        return r.json()
    r.raise_for_status()


def top_photo(hdr, section_id):
    """(image_url, listing_title) for the most-favorited active listing in a section."""
    best = None
    for page in range(MAX_PAGES):
        data = _get(hdr, f"/shops/{SHOP_ID}/shop-sections/listings",
                    shop_section_ids=section_id, limit=100, offset=page * 100)
        rows = data.get("results", [])
        for li in rows:
            if li.get("state", "active") != "active":
                continue
            score = (li.get("num_favorers") or 0, li.get("views") or 0)
            if best is None or score > best[0]:
                best = (score, li)
        if len(rows) < 100:
            break
    if not best:
        return None, None
    li = best[1]
    imgs = _get(hdr, f"/listings/{li['listing_id']}/images").get("results", [])
    if not imgs:
        return None, None
    img = min(imgs, key=lambda i: i.get("rank") or 99)
    return img.get("url_570xN") or img.get("url_fullxfull"), li.get("title", "")


def _label(tile):
    m = re.search(r'data-label="([^"]*)"', tile)
    if m:
        return htmlmod.unescape(m.group(1))
    m = re.search(r'<b>([^<]*)</b>', tile) or re.search(r'>([^<]+)<span>', tile)
    return htmlmod.unescape(m.group(1)).strip() if m else ""


def _old_img(tile):
    m = re.search(r'<img src="([^"]*)"', tile)
    return m.group(1) if m else PLACEHOLDER


def tile(title, label, href, count, img):
    e = htmlmod.escape
    return (f'<a class="sec" data-section="{e(title)}" data-label="{e(label)}" href="{e(href)}">'
            f'<img src="{e(img)}" alt="{e(label)}" loading="lazy">'
            f'<b>{e(label)}</b><span>{count}</span></a>')


def refresh(page, hdr, photos=True, log=print):
    """Return (new_html, report). page is the text of index.html."""
    shop = _get(hdr, f"/shops/{SHOP_ID}")
    base = (shop.get("url") or f"https://www.etsy.com/shop/{shop.get('shop_name')}").split("?")[0]
    sections = _get(hdr, f"/shops/{SHOP_ID}/sections").get("results", [])
    by_title = {s["title"].strip().lower(): s for s in sections}
    rows, missing = [], []

    def fix(m):
        t = m.group(0)
        title, label = htmlmod.unescape(m.group(1)), _label(m.group(0))
        sec = by_title.get(title.strip().lower())
        if not sec:
            missing.append(title)
            return t
        n = sec.get("active_listing_count", 0)
        img, pick = _old_img(t), ""
        if photos and n:
            try:
                url, pick = top_photo(hdr, sec["shop_section_id"])
                img = url or img
            except Exception as ex:          # keep last week's photo rather than fail
                log(f"  photo skipped for {title}: {ex}")
        href = f"{base}?section_id={sec['shop_section_id']}"
        rows.append({"Section": title, "Listings": n, "Top Pick": pick, "Link": href})
        log(f"  {label}: {n:,} listings" + (f", photo from \"{pick[:60]}\"" if pick else ""))
        return tile(title, label, href, f"{n:,}", img)

    page = TILE_RE.sub(fix, page)
    page = re.sub(r'href="https://www\.etsy\.com/shop/[^"?]+"', f'href="{base}"', page)
    total = shop.get("listing_active_count")
    if total:
        page = re.sub(r"<span data-total>[^<]*</span>", f"<span data-total>{total:,}</span>", page)
    page = re.sub(r"<span data-updated>[^<]*</span>",
                  f"<span data-updated>{datetime.date.today().isoformat()}</span>", page)
    on_page = {r["Section"].lower() for r in rows} | {x.lower() for x in missing}
    not_on_page = [s["title"] for s in sections if s["title"].strip().lower() not in on_page]
    return page, {"base": base, "total": total, "rows": rows, "missing": missing, "not_on_page": not_on_page}


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    key, secret = os.environ.get("ETSY_KEYSTRING"), os.environ.get("ETSY_SHARED_SECRET")
    if not (key and secret):
        cands = sys.argv[1:] + [r"D:\Google Drive\My APIs\etsy_tokens.json", os.path.join(here, "etsy_tokens.json")]
        tok_file = next((c for c in cands if os.path.exists(c)), None)
        if not tok_file:
            sys.exit("No ETSY_KEYSTRING / ETSY_SHARED_SECRET and no etsy_tokens.json found.")
        tok = json.load(open(tok_file, encoding="utf-8"))
        key, secret = tok["keystring"], tok["shared_secret"]
    path = os.path.join(here, "index.html")
    with open(path, encoding="utf-8") as fh:
        page = fh.read()
    page, rep = refresh(page, headers(key, secret))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    print(f"Updated {len(rep['rows'])} section tile(s) for {rep['base']}")
    if rep["missing"]:
        print("No Etsy section with these exact names (left unchanged):", ", ".join(rep["missing"]))
    if rep["not_on_page"]:
        print("Etsy sections not on the homepage:", ", ".join(rep["not_on_page"]))


if __name__ == "__main__":
    main()
