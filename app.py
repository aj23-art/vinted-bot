import time
import random
import asyncio
import aiohttp
import requests
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

# ====== Bestehende Vars behalten ======
anon_id = None
last_token_time = 0
request_counter = 0

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1"
]

CATEGORIES = {
    "bedruckte_jeans": ["Ed Hardy", "Von Dutch", "Affliction", "True Religion bedruckt"],
    "ralph_lauren": ["Ralph Lauren", "Polo Bear", "Polo Ralph Lauren"],
    "true_adidas": ["True Religion", "Adidas Jogger", "Adidas Originals Jogger"],
    "random": ["Carhartt", "Stussy", "Diesel"]
}

WHITELIST = ["ed hardy", "von dutch", "affliction", "true religion", "ralph lauren", "polo bear", "polo ralph", "carhartt", "stussy", "diesel", "adidas"]

DEALS = []
SCANNED_TOTAL = 0

def get_random_ua():
    return random.choice(USER_AGENTS)

def get_new_anon_id():
    global anon_id, last_token_time, request_counter
    try:
        headers = {"User-Agent": get_random_ua(), "Accept": "application/json"}
        # Vinted anon endpoint - holt neue anon_id + cookies
        r = requests.get("https://www.vinted.de/api/auth", headers=headers, timeout=10)
        # anon_id kommt oft als Cookie oder JSON
        if "anon_id" in r.cookies:
            anon_id = r.cookies["anon_id"]
        elif r.headers.get("x-anon-id"):
            anon_id = r.headers.get("x-anon-id")
        else:
            # fallback - generiere temporäre id, wird trotzdem mit UA rotiert
            anon_id = f"{int(time.time()*1000)}_{random.randint(100000,999999)}"
        
        last_token_time = time.time()
        request_counter = 0
        print(f"[TOKEN] Neue anon_id: {anon_id[:20]}... UA: {headers['User-Agent'][:30]}")
        return anon_id
    except Exception as e:
        print(f"[TOKEN ERROR] {e}")
        anon_id = f"fallback_{int(time.time())}"
        last_token_time = time.time()
        return anon_id

# Initial Token holen
get_new_anon_id()

async def fetch_vinted_page(session, search_text, page, category, sem):
    global request_counter, anon_id, last_token_time, SCANNED_TOTAL
    async with sem:
        # Alle 50 Requests Token neu
        if request_counter >= 50 or (time.time() - last_token_time) > 600:
            # Sync call in async - kurz blocken ist ok für Token Refresh
            await asyncio.to_thread(get_new_anon_id)

        headers = {
            "User-Agent": get_random_ua(),
            "Accept": "application/json, text/plain, */*",
            "x-anon-id": anon_id or "",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"
        }
        
        url = f"https://www.vinted.de/api/v2/catalog/items?search_text={search_text}&page={page}&per_page=96&order=newest_first"
        
        for attempt in range(3):
            try:
                request_counter += 1
                async with session.get(url, headers=headers, timeout=15) as resp:
                    if resp.status in [403, 429]:
                        print(f"[BLOCK] {resp.status} bei {search_text} p{page} - warte 60s")
                        await asyncio.sleep(60)
                        await asyncio.to_thread(get_new_anon_id)
                        headers["User-Agent"] = get_random_ua()
                        headers["x-anon-id"] = anon_id
                        continue
                    
                    if resp.status != 200:
                        print(f"[HTTP {resp.status}] {search_text}")
                        await asyncio.sleep(2)
                        continue
                    
                    data = await resp.json()
                    items = data.get("items", [])
                    SCANNED_TOTAL += len(items)
                    return [(item, category, search_text) for item in items]

            except Exception as e:
                print(f"[FETCH ERROR] {search_text} p{page}: {e}")
                await asyncio.sleep(2 + attempt)
        
        return []

def is_good_deal(item, category):
    try:
        title = (item.get("title") or "").lower()
        brand = (item.get("brand_title") or item.get("brand") or "").lower()
        price_str = item.get("price") or item.get("total_item_price") or {}
        if isinstance(price_str, dict):
            price = float(price_str.get("amount", 999))
        else:
            price = float(str(price_str).replace("€","").replace(",",".").strip() or 999)

        # 1. Whitelist Check
        full_text = f"{title} {brand}"
        in_whitelist = any(w in full_text for w in WHITELIST)
        if not in_whitelist:
            return False
        
        # 2. Preis < 25
        if price >= 25:
            return False
        
        # 3. Online < 10 Min
        # Vinted gibt created_at oder updated_at als ISO oder timestamp
        created = item.get("created_at_ts") or item.get("created_at") or 0
        if isinstance(created, str):
            try:
                dt = datetime.fromisoformat(created.replace("Z","+00:00"))
                diff = datetime.now(dt.tzinfo) - dt
                if diff > timedelta(minutes=10):
                    return False
            except:
                pass # wenn kein Datum, lassen wir durch für Test
        elif isinstance(created, (int,float)) and created > 0:
            # timestamp in Sekunden
            if time.time() - created > 600: # 10 Min = 600s
                return False

        return True
    except:
        return False

async def scan_all_categories():
    global DEALS
    sem = asyncio.Semaphore(15) # 15 parallel wie gewünscht
    async with aiohttp.ClientSession() as session:
        tasks = []
        # 4x25% - 8 Seiten pro Kategorie = 32 Seiten = ~3000 Items pro Scan (für TV)
        pages_per_category = 8
        
        for category, queries in CATEGORIES.items():
            for query in queries:
                for page in range(1, pages_per_category + 1):
                    tasks.append(fetch_vinted_page(session, query, page, category, sem))
        
        results = await asyncio.gather(*tasks)
        flat = [x for sub in results for x in sub]
        
        # Deal Logik filtern
        new_deals = []
        for item, category, query in flat:
            if is_good_deal(item, category):
                new_deals.append({
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "price": item.get("price"),
                    "brand": item.get("brand_title"),
                    "url": item.get("url") or f"https://www.vinted.de/items/{item.get('id')}",
                    "category": category, # Kategorie in JSON speichern
                    "search_query": query,
                    "created_at": item.get("created_at"),
                    "found_at": datetime.now().isoformat()
                })
        
        DEALS = new_deals[:100] # Top 100 behalten
        print(f"[SCAN DONE] Geprüft: {SCANNED_TOTAL} | Deals: {len(DEALS)}")
        return DEALS

# ====== 2. Render Uptime Endpoints ======
@app.get("/health")
def health():
    return jsonify({"status": "ok", "anon_id": anon_id[:10] if anon_id else None, "requests": request_counter, "last_token": last_token_time})

@app.get("/ping")
def ping():
    return jsonify({"pong": True, "time": time.time(), "scanned": SCANNED_TOTAL})

@app.route("/api/deals")
def api_deals():
    return jsonify({
        "deals": DEALS,
        "scanned_total": SCANNED_TOTAL,
        "categories": list(CATEGORIES.keys()),
        "last_scan": datetime.now().isoformat(),
        "anon_id_age": int(time.time() - last_token_time)
    })

@app.route("/api/scan")
def api_scan():
    # Async Scan triggern
    asyncio.run(scan_all_categories())
    return jsonify({"ok": True, "deals_found": len(DEALS), "scanned": SCANNED_TOTAL})

@app.route("/")
def index():
    html = """
    <h1>Vinted Flip - 800 Scan + Anti-Detect PRO</h1>
    <p>Geprüft: {{total}} Artikel | Deals: {{deals_len}}</p>
    <p>Kategorien: bedruckte_jeans (25%) | ralph_lauren (25%) | true_adidas (25%) | random (25%)</p>
    <p>Letzter Token: vor {{age}}s | Requests seit Refresh: {{req}}</p>
    <a href="/api/scan">Jetzt scannen</a> | <a href="/api/deals">JSON</a> | <a href="/ping">/ping</a>
    <hr>
    {% for d in deals %}
    <div>[{{d.category}}] <a href="{{d.url}}" target="_blank">{{d.title}}</a> - {{d.price}} - {{d.brand}}</div>
    {% endfor %}
    """
    return render_template_string(html, total=SCANNED_TOTAL, deals_len=len(DEALS), deals=DEALS, age=int(time.time()-last_token_time), req=request_counter)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=
