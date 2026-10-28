"""Points the homepage category buttons at the real Etsy shop sections and refreshes the counts.

Runs weekly on GitHub (see .github/workflows/update-sections.yml) using two repo secrets,
ETSY_KEYSTRING and ETSY_SHARED_SECRET. Also runs on your PC:
    python update_site_sections.py [path\\to\\etsy_tokens.json]
Read-only: two Etsy calls, no sign-in token needed, nothing on Etsy changes.
"""
import datetime
import json
import os
import re
import sys

import requests

SHOP_ID = "45435667"
API = "https://api.etsy.com/v3/application"
here = os.path.dirname(os.path.abspath(__file__))

key, secret = os.environ.get("ETSY_KEYSTRING"), os.environ.get("ETSY_SHARED_SECRET")
if not (key and secret):
    cands = sys.argv[1:] + [r"D:\Google Drive\My APIs\etsy_tokens.json", os.path.join(here, "etsy_tokens.json")]
    tok_file = next((c for c in cands if os.path.exists(c)), None)
    if not tok_file:
        sys.exit("No ETSY_KEYSTRING / ETSY_SHARED_SECRET and no etsy_tokens.json found.")
    tok = json.load(open(tok_file, encoding="utf-8"))
    key, secret = tok["keystring"], tok["shared_secret"]
hdr = {"x-api-key": f"{key}:{secret}"}

shop = requests.get(f"{API}/shops/{SHOP_ID}", headers=hdr, timeout=30)
shop.raise_for_status()
shop = shop.json()
base = (shop.get("url") or f"https://www.etsy.com/shop/{shop.get('shop_name')}").split("?")[0]
secs = requests.get(f"{API}/shops/{SHOP_ID}/sections", headers=hdr, timeout=30)
secs.raise_for_status()
by_title = {s["title"].strip().lower(): s for s in secs.json().get("results", [])}

path = os.path.join(here, "index.html")
html = open(path, encoding="utf-8").read()
done, missing = 0, []


def fix(m):
    global done
    title, label = m.group(1), m.group(3)
    sec = by_title.get(title.strip().lower())
    if not sec:
        missing.append(title)
        return m.group(0)
    done += 1
    return (f'<a class="sec" data-section="{title}" href="{base}?section_id={sec["shop_section_id"]}">'
            f'{label}<span>{sec.get("active_listing_count", 0):,}</span></a>')


html = re.sub(r'<a class="sec" data-section="([^"]+)" href="([^"]*)">([^<]+)<span>[^<]*</span></a>', fix, html)
html = re.sub(r'href="https://www\.etsy\.com/shop/[^"?]+"', f'href="{base}"', html)
total = shop.get("listing_active_count")
if total:
    html = re.sub(r"<span data-total>[^<]*</span>", f"<span data-total>{total:,}</span>", html)
html = re.sub(r"<span data-updated>[^<]*</span>",
              f"<span data-updated>{datetime.date.today().isoformat()}</span>", html)
open(path, "w", encoding="utf-8").write(html)
print(f"Linked {done} section(s) to {base}")
if missing:
    print("No Etsy section with these exact names (left as shop searches):", ", ".join(missing))
