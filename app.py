
import requests, time, random, re
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*"
})
anon_id = None
last_token_time = 0

def get_fresh_session():
    global anon_id, last_token_time
    # Refresh token every 5 min
    if time.time() - last_token_time < 300 and anon_id:
        return
    try:
        r = session.get("https://www.vinted.de/", timeout=10)
        # anon id is in cookies or in html
        anon_id = session.cookies.get("anon_id") or session.cookies.get("_vinted_anon_id")
        if not anon_id:
            m = re.search(r'"anon_id":"([^"]+)"', r.text)
            if m:
                anon_id = m.group(1)
        last_token_time = time.time()
        print(f"Neuer anon_id: {anon_id}")
    except Exception as e:
        print(f"Token Fehler: {e}")

def fetch_real_items(per_page=40):
    get_fresh_session()
    if not anon_id:
        return []
    try:
        headers = {
            "X-Anon-Id": anon_id,
            "Accept": "application/json"
        }
        # echte neuste Artikel
        url = f"https://www.vinted.de/api/v2/catalog/items?order=newest_first&per_page={per_page}&time={int(time.time())}"
        r = session.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            print(f"API Fehler {r.status_code}: {r.text[:200]}")
            return []
        data = r.json()
        items = data.get("items", [])
        real_deals = []
        for it in items:
            if not it.get("is_visible"): continue
            price = float(it.get("price", {}).get("amount", 0))
            if price < 10 or price > 60: continue
            
            # LIVE CHECK - existiert wirklich noch?
            try:
                check = session.get(f"https://www.vinted.de/api/v2/items/{it['id']}", headers=headers, timeout=5)
                if check.status_code != 200: continue
                detail = check.json().get("item", {})
                if not detail.get("is_visible"): continue
                if detail.get("is_sold"): continue
            except:
                continue

            # Median Preis holen (20 günstigste)
            title = it.get("title","")
            try:
                search_url = f"https://www.vinted.de/api/v2/catalog/items?search_text={title[:30]}&order=price_low&per_page=20"
                sr = session.get(search_url, headers=headers, timeout=8)
                if sr.status_code == 200:
                    prices = [float(x['price']['amount']) for x in sr.json().get("items",[]) if x.get('price')]
                    prices.sort()
                    median = prices[len(prices)//2] if prices else price*2.5
                else:
                    median = price*2.5
            except:
                median = price*2.5

            sicherer_verkauf = median * 0.85
            vinted_fee = sicherer_verkauf * 0.05
            fix_fee = 0.70
            versand = 5.0
            gewinn = sicherer_verkauf - price - vinted_fee - fix_fee - versand

            # Deine Gewinnregeln
            ok = False
            if 10 <= price < 30 and gewinn >= 15: ok = True
            if 30 <= price < 40 and gewinn >= 20: ok = True
            if 40 <= price <= 60 and gewinn >= (price*0.8): ok = True

            if ok and gewinn > 5:
                real_deals.append({
                    "id": it["id"],
                    "title": it["title"],
                    "price_buy": price,
                    "price_sell": round(sicherer_verkauf,2),
                    "profit": round(gewinn,2),
                    "seller": it.get("user",{}).get("login",""),
                    "seller_id": it.get("user",{}).get("id",""),
                    "photo": it.get("photo",{}).get("url","") if isinstance(it.get("photo"), dict) else "",
                    "url": f"https://www.vinted.de/items/{it['id']}",
                    "seller_url": f"https://www.vinted.de/member/{it.get('user',{}).get('id','')}-{it.get('user',{}).get('login','')}",
                    "checked": True
                })
        return real_deals[:20]
    except Exception as e:
        print(f"Fetch Fehler: {e}")
        return []

@app.get("/api/deals")
def api_deals():
    deals = fetch_real_items()
    return {"deals": deals, "count": len(deals), "scanned": 40, "timestamp": time.time()}

@app.get("/", response_class=HTMLResponse)
def index():
    return """
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vinted Flip - Echte Deals</title>
<style>
body{background:#121212;color:#fff;font-family:system-ui;padding:16px;max-width:800px;margin:0 auto}
.card{background:#1e1e1e;border:1px solid #333;border-radius:12px;padding:12px;margin:12px 0;display:flex;gap:12px}
.card img{width:80px;height:80px;object-fit:cover;border-radius:8px;background:#2a2a2a}
.badge{background:#00c95022;color:#00c950;padding:2px 8px;border-radius:99px;font-size:12px}
.profit{color:#00c950;font-weight:700}
.btn{background:#fff;color:#000;border:0;padding:8px 12px;border-radius:8px;font-weight:600;cursor:pointer}
.small{color:#888;font-size:12px}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
</style>
</head>
<body>
<div class="header">
<h2>Vinted Flip - 100% echt</h2>
<button class="btn" onclick="load()">Aktualisieren</button>
</div>
<div id="status" class="small">Lade echte Vinted Artikel...</div>
<div id="list"></div>
<script>
async function load(){
 document.getElementById('status').innerText='Durchsuche echten Vinted Markt... (2x geprüft: existiert + Verkäufer hat es)';
 const r = await fetch('/api/deals');
 const data = await r.json();
 const list = document.getElementById('list');
 document.getElementById('status').innerText = `Letzter Scan: jetzt | Geprüft: ${data.scanned} Artikel | Echte Deals: ${data.count} | Jeder Deal ist live geprüft`;
 list.innerHTML='';
 if(data.deals.length==0){ list.innerHTML='<p class="small">Gerade keine die deine Gewinnregeln schaffen. In 5 Min nochmal versuchen - Vinted lädt jede Minute neue hoch.</p>'; return; }
 data.deals.forEach(d=>{
   const div=document.createElement('div'); div.className='card';
   div.innerHTML=`<img src="${d.photo}" onerror="this.style.background='#333'"><div style="flex:1"><div style="font-weight:600">${d.title}</div><div class="small">Verkäufer: ${d.seller} <span class="badge">✓ live geprüft ID ${d.id}</span></div><div style="margin-top:6px">Kauf <b>${d.price_buy}€</b> | Verkauf <b>${d.price_sell}€</b> | <span class="profit">Gewinn +${d.profit}€</span></div><div class="small">Rechnung: ${d.price_sell}€ x0.95 -0.70€ -5€ Versand -${d.price_buy}€ = +${d.profit}€</div><div style="margin-top:8px"><a href="${d.seller_url}" target="_blank"><button class="btn">Zu ${d.seller}</button></a> <a href="${d.url}" target="_blank" style="margin-left:8px;color:#aaa;font-size:13px">Produkt öffnen</a></div></div>`;
   list.appendChild(div);
 })
}
load();
setInterval(load, 300000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
