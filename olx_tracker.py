#!/usr/bin/env python3
"""Śledzenie cen ogłoszeń OLX: historia cen, obniżki i statystyki wyświetlane na stronie WWW.

Uruchomienie:
    python olx_tracker.py          # normalny przebieg: pobiera ogłoszenia, zapisuje historię, generuje site/index.html
    python olx_tracker.py --dry    # tylko pokazuje, co by zrobił; nic nie zapisuje

Zmienne środowiskowe (opcjonalne):
    SEARCH_URL   link do wyszukiwania OLX; jeśli ustawiony, ma pierwszeństwo przed plikiem search_url.txt
"""
import html as htmllib
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

ROOT = Path(__file__).resolve().parent
SEARCH_URL_FILE = ROOT / "search_url.txt"
DB_FILE = ROOT / "data" / "history.json"
SITE_DIR = ROOT / "site"
DEBUG_FILE = ROOT / "debug_last.html"
MAX_PAGES = 10
RECENT_DROPS = 10

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.5",
}


# ----------------------------------------------------------------- pobieranie

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def page_url(base, page):
    parts = urlparse(base)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "page"]
    if page > 1:
        query.append(("page", str(page)))
    return urlunparse(parts._replace(query=urlencode(query)))


def to_number(text):
    m = re.search(r"\d[\d\s ]*(?:[.,]\d+)?", text or "")
    if not m:
        return None
    cleaned = re.sub(r"[\s ]", "", m.group()).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_state(page_html):
    """Wyciąga dane osadzone w stronie (window.__PRERENDERED_STATE__)."""
    m = re.search(r"__PRERENDERED_STATE__\s*=\s*", page_html)
    if not m:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(page_html[m.end():])
        return json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return None


def norm_state_ad(a):
    price_obj = a.get("price") or {}
    regular = price_obj.get("regularPrice") or {}
    price = regular.get("value")
    if not isinstance(price, (int, float)):
        price = to_number(price_obj.get("displayValue") or regular.get("displayValue") or "")
    url = a.get("url") or ""
    if url.startswith("/"):
        url = "https://www.olx.pl" + url
    location = a.get("location") or {}
    return {
        "id": str(a["id"]),
        "title": (a.get("title") or "").strip(),
        "url": url,
        "price": float(price) if price is not None else None,
        "created": a.get("createdTime"),
        "refreshed": a.get("lastRefreshTime"),
        "location": location.get("cityName") or "",
    }


def parse_cards(page_html):
    """Plan B: wyciąganie ogłoszeń z kart HTML, gdyby dane osadzone zniknęły."""
    ads = []
    for part in page_html.split('data-cy="l-card"')[1:]:
        href = re.search(r'href="(/d/oferta/[^"]+)"', part)
        if not href:
            continue
        title = re.search(r"<h[3-6][^>]*>(.*?)</h[3-6]>", part, re.S)
        price = re.search(r'data-testid="ad-price"[^>]*>(.*?)</p>', part, re.S)
        url = "https://www.olx.pl" + href.group(1).split("#")[0]
        ad_id = re.search(r"-ID(\w+)\.html", url)
        ads.append({
            "id": ad_id.group(1) if ad_id else url,
            "title": htmllib.unescape(re.sub(r"<[^>]+>", "", title.group(1))).strip() if title else "",
            "url": url,
            "price": to_number(re.sub(r"<[^>]+>", "", price.group(1))) if price else None,
            "created": None,
            "refreshed": None,
            "location": "",
        })
    return ads


def scrape(base_url):
    session = requests.Session()
    session.headers.update(HEADERS)
    ads, page, total_pages = {}, 1, 1
    while page <= min(total_pages, MAX_PAGES):
        resp = session.get(page_url(base_url, page), timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"OLX zwrócił HTTP {resp.status_code} dla strony {page} wyników")
        state = extract_state(resp.text)
        found = []
        if state is not None:
            listing = (state.get("listing") or {}).get("listing") or {}
            found = [norm_state_ad(a) for a in listing.get("ads", []) if a.get("id")]
            total_pages = listing.get("totalPages") or total_pages
        if not found and state is None:
            found = parse_cards(resp.text)
            if not found:
                DEBUG_FILE.write_text(resp.text, encoding="utf-8")
                raise RuntimeError(
                    "Nie udało się odczytać ogłoszeń ze strony (zmiana układu OLX albo blokada). "
                    "Zapisano debug_last.html."
                )
        for ad in found:
            ads[ad["id"]] = ad
        page += 1
        if page <= min(total_pages, MAX_PAGES):
            time.sleep(random.uniform(3, 6))
    return ads


# ----------------------------------------------------------------- baza danych

def load_db():
    if DB_FILE.exists():
        return json.loads(DB_FILE.read_text(encoding="utf-8"))
    return {"initialized": False, "ads": {}}


def save_db(db):
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    DB_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")


def update_db(db, ads):
    """Aktualizuje historię; zwraca listę zdarzeń: nowe ogłoszenia i obniżki cen."""
    now = now_iso()
    db["last_check"] = now
    events = []
    for ad_id, ad in ads.items():
        rec = db["ads"].get(ad_id)
        if rec is None:
            rec = {
                "id": ad_id, "title": ad["title"], "url": ad["url"], "location": ad["location"],
                "first_seen": now, "last_seen": now, "created": ad["created"],
                "refreshed": ad["refreshed"], "price": ad["price"],
                "price_history": [[now, ad["price"]]],
                "drops": 0, "price_changes": 0, "title_changes": 0, "refresh_count": 0,
                "active": True,
            }
            db["ads"][ad_id] = rec
            events.append({"type": "new", "rec": rec})
            continue

        old_price = rec["price"]
        new_price = ad["price"]
        if new_price is not None and new_price != old_price:
            if old_price is not None:
                rec["price_changes"] += 1
                if new_price < old_price:
                    rec["drops"] += 1
                    events.append({"type": "drop", "rec": rec, "old": old_price})
            rec["price"] = new_price
            rec["price_history"].append([now, new_price])
        if ad["title"] and ad["title"] != rec["title"]:
            rec["title_changes"] += 1
            rec["title"] = ad["title"]
        if ad["refreshed"] and ad["refreshed"] != rec.get("refreshed"):
            if rec.get("refreshed"):
                rec["refresh_count"] += 1
            rec["refreshed"] = ad["refreshed"]
        if not rec.get("created") and ad["created"]:
            rec["created"] = ad["created"]
        rec["url"] = ad["url"] or rec["url"]
        rec["last_seen"] = now
        rec["active"] = True
        rec.pop("ended", None)

    if ads:  # nie oznaczaj wszystkiego jako zakończone, jeśli pobranie zwróciło pustkę
        for ad_id, rec in db["ads"].items():
            if ad_id not in ads and rec["active"]:
                rec["active"] = False
                rec["ended"] = now
    return events


# ----------------------------------------------------------------- strona WWW

def parse_dt(text):
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_dt(text):
    dt = parse_dt(text)
    if not dt:
        return ""
    try:
        from zoneinfo import ZoneInfo
        dt = dt.astimezone(ZoneInfo("Europe/Warsaw"))
    except Exception:
        pass
    return dt.strftime("%d.%m.%Y %H:%M")


def days_live(rec):
    start = parse_dt(rec.get("created")) or parse_dt(rec.get("first_seen"))
    end = datetime.now(timezone.utc) if rec["active"] else parse_dt(rec.get("last_seen"))
    if not start or not end:
        return None
    return max((end - start).days, 0)


def fmt_price(value):
    if value is None:
        return "brak ceny"
    return f"{value:,.0f}".replace(",", " ") + " zł"


def esc(text):
    return htmllib.escape(str(text or ""), quote=True)


def sparkline(history):
    prices = [p for _, p in history if p is not None]
    if len(prices) < 2:
        return ""
    w, h, pad = 110, 28, 3
    lo, hi = min(prices), max(prices)
    span = (hi - lo) or 1
    n = len(prices)
    pts = []
    for i, p in enumerate(prices):
        x = pad + i * (w - 2 * pad) / (n - 1)
        y = h - pad - (p - lo) * (h - 2 * pad) / span
        pts.append(f"{x:.1f},{y:.1f}")
    last = pts[-1].split(",")
    return (
        f'<svg class="spark" viewBox="0 0 {w} {h}" role="img" aria-label="Historia ceny">'
        f'<polyline points="{" ".join(pts)}" fill="none" stroke="currentColor" stroke-width="1.8" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{last[0]}" cy="{last[1]}" r="2.6" fill="currentColor"/></svg>'
    )


def recent_drops(recs):
    drops = []
    for r in recs:
        hist = r["price_history"]
        for i in range(1, len(hist)):
            prev, cur = hist[i - 1][1], hist[i][1]
            if prev is not None and cur is not None and cur < prev:
                drops.append((hist[i][0], r, prev, cur))
    drops.sort(key=lambda d: d[0], reverse=True)
    return drops[:RECENT_DROPS]


def render_card(r):
    history = r["price_history"]
    first = history[0][1]
    days = days_live(r)
    seen = parse_dt(r["first_seen"])
    delta_html = ""
    if r["price"] is not None and first is not None and r["price"] != first:
        diff = r["price"] - first
        cls = "good" if diff < 0 else "bad"
        sign = "−" if diff < 0 else "+"
        delta_html = f'<span class="delta {cls}">{sign}{fmt_price(abs(diff))} od pierwszej ceny ({fmt_price(first)})</span>'
    badge_drop = f'<span class="badge hot">{r["drops"]}× obniżka</span>' if r["drops"] else ""
    created = fmt_dt(r.get("created")) or fmt_dt(r["first_seen"])
    return f"""
<article class="card" data-drops="{r['drops']}" data-price="{r['price'] or 0:.0f}" data-days="{days if days is not None else 0}" data-seen="{int(seen.timestamp()) if seen else 0}">
  <a class="title" href="{esc(r['url'])}" target="_blank" rel="noopener">{esc(r['title'] or r['id'])}</a>
  <div class="row"><span class="price">{fmt_price(r['price'])}</span>{badge_drop}</div>
  {delta_html}
  <div class="spark-wrap">{sparkline(history)}</div>
  <dl class="stats">
    <div><dt>Obniżki</dt><dd>{r['drops']}</dd></div>
    <div><dt>Zmiany ceny</dt><dd>{r['price_changes']}</dd></div>
    <div><dt>Zmiany tytułu</dt><dd>{r['title_changes']}</dd></div>
    <div><dt>Odświeżenia</dt><dd>{r['refresh_count']}</dd></div>
    <div><dt>Dni w sprzedaży</dt><dd>{'' if days is None else days}</dd></div>
  </dl>
  <div class="meta">{esc(r['location'])}{' · ' if r['location'] else ''}od {created}</div>
</article>"""


PAGE_CSS = """
:root{--bg:#f5f6f8;--card:#fff;--text:#1b1f24;--muted:#66707c;--line:#e3e6ea;--accent:#1a6fd6;--good:#0b7a3f;--bad:#b3261e;--hot:#d9480f;--hotbg:#fff1e8}
@media (prefers-color-scheme:dark){:root{--bg:#14171b;--card:#1d2127;--text:#e8eaed;--muted:#9aa3ad;--line:#2d333b;--accent:#6aa8ff;--good:#4cc582;--bad:#ff8a80;--hot:#ff9a62;--hotbg:#3a2418}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:1.35rem;margin:4px 0 2px}
h2{font-size:1.05rem;margin:28px 0 10px}
.sub{color:var(--muted);font-size:.88rem}
.tiles{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:16px 0}
@media(min-width:640px){.tiles{grid-template-columns:repeat(4,1fr)}}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.tile b{display:block;font-size:1.5rem;line-height:1.2}
.tile span{color:var(--muted);font-size:.82rem}
.drops{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.drops li{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--hot);border-radius:10px;padding:10px 12px}
.drops a{color:var(--text);font-weight:600;text-decoration:none}
.drops .what{color:var(--good);font-weight:600}
.drops .when{color:var(--muted);font-size:.82rem}
.toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:28px 0 10px}
.toolbar h2{margin:0}
select{font:inherit;color:var(--text);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 8px;max-width:60%}
.grid{display:grid;grid-template-columns:1fr;gap:12px}
@media(min-width:640px){.grid{grid-template-columns:repeat(2,1fr)}}
@media(min-width:960px){.grid{grid-template-columns:repeat(3,1fr)}}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;display:flex;flex-direction:column;gap:6px}
.title{color:var(--accent);font-weight:600;text-decoration:none;overflow-wrap:anywhere}
.row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.price{font-size:1.4rem;font-weight:700}
.badge{font-size:.75rem;font-weight:600;border-radius:999px;padding:2px 9px}
.badge.hot{color:var(--hot);background:var(--hotbg)}
.delta{font-size:.85rem}.good{color:var(--good)}.bad{color:var(--bad)}
.spark-wrap{height:28px;color:var(--accent)}
.spark{width:110px;height:28px}
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:4px;margin:4px 0 0;text-align:center}
.stats dt{font-size:.66rem;color:var(--muted);line-height:1.15}
.stats dd{margin:2px 0 0;font-weight:700}
.meta{color:var(--muted);font-size:.8rem}
details{margin-top:28px}
summary{cursor:pointer;font-weight:600}
.ended{list-style:none;margin:10px 0 0;padding:0;display:grid;gap:6px}
.ended li{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 12px;font-size:.9rem}
.ended a{color:var(--text);text-decoration:none;font-weight:600}
.empty{color:var(--muted)}
footer{margin:32px 0 8px;color:var(--muted);font-size:.78rem}
"""

PAGE_JS = """
(function(){
  var grid=document.getElementById('active'), sel=document.getElementById('sort');
  if(!grid||!sel)return;
  sel.addEventListener('change',function(){
    var p=sel.value.split(':'), key=p[0], dir=p[1]==='asc'?1:-1;
    var cards=Array.prototype.slice.call(grid.children);
    cards.sort(function(a,b){return (a.dataset[key]-b.dataset[key])*dir;});
    cards.forEach(function(c){grid.appendChild(c);});
  });
})();
"""


def render_html(db):
    recs = list(db["ads"].values())
    active = sorted((r for r in recs if r["active"]), key=lambda r: (-r["drops"], r["price"] or 0))
    ended = sorted((r for r in recs if not r["active"]), key=lambda r: r.get("last_seen", ""), reverse=True)[:30]
    total_drops = sum(r["drops"] for r in recs)
    with_drops = sum(1 for r in active if r["drops"])
    ages = [d for d in (days_live(r) for r in active) if d is not None]
    avg_days = round(sum(ages) / len(ages)) if ages else 0

    drops_html = "".join(
        f'<li><a href="{esc(r["url"])}" target="_blank" rel="noopener">{esc(r["title"])}</a><br>'
        f'<span class="what">{fmt_price(prev)} → {fmt_price(cur)} (−{fmt_price(prev - cur)}, '
        f'−{(prev - cur) / prev * 100:.1f}%)</span> <span class="when">· {fmt_dt(ts)}</span></li>'
        for ts, r, prev, cur in recent_drops(recs)
    ) or '<li class="empty">Na razie żadnych obniżek.</li>'

    cards_html = "".join(render_card(r) for r in active) or '<p class="empty">Brak aktywnych ogłoszeń.</p>'

    ended_html = ""
    if ended:
        items = "".join(
            f'<li><a href="{esc(r["url"])}" target="_blank" rel="noopener">{esc(r["title"])}</a><br>'
            f'<span class="sub">ostatnia cena {fmt_price(r["price"])} · obniżek: {r["drops"]} · '
            f'dni w sprzedaży: {days_live(r) if days_live(r) is not None else ""}</span></li>'
            for r in ended
        )
        ended_html = f'<details><summary>Zakończone ogłoszenia ({len(ended)})</summary><ul class="ended">{items}</ul></details>'

    return f"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Monitor cen OLX</title>
<style>{PAGE_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>Monitor cen OLX</h1>
  <div class="sub">Ostatnie sprawdzenie: {fmt_dt(db.get('last_check'))} (czas polski)</div>

  <div class="tiles">
    <div class="tile"><b>{len(active)}</b><span>aktywnych ogłoszeń</span></div>
    <div class="tile"><b>{with_drops}</b><span>z obniżką ceny</span></div>
    <div class="tile"><b>{total_drops}</b><span>obniżek łącznie</span></div>
    <div class="tile"><b>{avg_days}</b><span>śr. dni w sprzedaży</span></div>
  </div>

  <h2>Ostatnie obniżki cen</h2>
  <ul class="drops">{drops_html}</ul>

  <div class="toolbar">
    <h2>Aktywne ogłoszenia</h2>
    <select id="sort" aria-label="Sortowanie">
      <option value="drops:desc">Najwięcej obniżek</option>
      <option value="price:asc">Cena rosnąco</option>
      <option value="price:desc">Cena malejąco</option>
      <option value="days:desc">Najdłużej w sprzedaży</option>
      <option value="seen:desc">Najnowsze</option>
    </select>
  </div>
  <div class="grid" id="active">{cards_html}</div>

  {ended_html}

  <footer>Odświeżenia = ile razy zmienił się czas ostatniego odświeżenia/edycji ogłoszenia. Dni = od publikacji
  (lub od pierwszego zobaczenia przez skrypt). Dane aktualizują się automatycznie co kilka godzin.</footer>
</div>
<script>{PAGE_JS}</script>
</body>
</html>
"""


def write_site(db):
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "index.html").write_text(render_html(db), encoding="utf-8")


# ----------------------------------------------------------------- main

def get_search_url():
    env_url = os.environ.get("SEARCH_URL", "").strip()
    if env_url:
        return env_url
    return SEARCH_URL_FILE.read_text(encoding="utf-8").strip()


def main():
    dry = "--dry" in set(sys.argv[1:])
    ads = scrape(get_search_url())
    print(f"Pobrano ogłoszeń: {len(ads)}")

    db = load_db()
    events = update_db(db, ads)
    drops = [e for e in events if e["type"] == "drop"]
    new_count = sum(e["type"] == "new" for e in events)
    print(f"Obniżki cen: {len(drops)}, nowe ogłoszenia: {new_count}")
    for e in drops:
        print(f"  OBNIŻKA: {e['rec']['title']}: {fmt_price(e['old'])} -> {fmt_price(e['rec']['price'])}")

    if dry:
        return
    db["initialized"] = True
    save_db(db)
    write_site(db)
    print(f"Zapisano stronę: {SITE_DIR / 'index.html'}")


if __name__ == "__main__":
    main()
