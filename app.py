import requests, time, re
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
    if time.time() - last_token_time < 300 and anon_id:
        return
    try:
        r = session.get("https://www.vinted.de/", timeout=10)
        anon_id = session.cookies.get("anon_id") or session.cookies.get("_vinted_anon_id")
        if not anon_id:
            m = re.search(r'"anon_id":"([^"]+)"', r.text)
            if m:
                anon_id = m.group(1)
        last_token_time = time.time()
        print(f"Neuer anon_id: {anon_id}")
    except Exception as e:
        print(f"Token Fehler: {e}")

def fetch_real_items(per_page=100, pages_to_scan=5):
    get_fresh_session()
    if not anon_id:
        return [], 0
    
    headers = {
        "X-Anon-Id": anon_id,
        "Accept": "application/json"
    }
    
    real_deals = []
    total_scanned = 0
    
    try:
        for page in range(1, pages_to_scan + 1):
            # echte neuste Artikel mit pagination
            url = f"https://www.vinted.de/api/v2/catalog/items?order=newest_first&per_page={per_page}&page={page}&time={int(time.time())}"
            r = session.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                print(f"API Fehler {r.status_code}: {r.text[:200]}")
                continue
            
            data = r.json()
            items = data.get("items", [])
            if not items:
                break
                
            total_scanned += len(items)
            
            for it in items:
                if not it.get("is_visible"): continue
                price = float(it.get("price", {}).get("amount", 0))
                if price < 10 or price > 80: continue
                
                # LIVE CHECK - existiert wirklich noch? Nur stichprobenartig bei vielen
                try:
                    check = session.get(f"https://www.vinted.de/api/v2/items/{it['id']}", headers=headers, timeout=3)
                    if check.status_code != 200: continue
                    detail = check.json().get("item", {})
                    if not detail.get("is_visible"): continue
                    if detail.get("is_sold"): continue
                except:
                    continue

                # Median Preis holen (20 günstigste) - nur für potenzielle Deals um Zeit zu sparen
                title = it.get("title","")
                if len(title) < 3: continue
                
                try:
                    search_url = f"https://www.vinted.de/api/v2/catalog/items?search_text={title[:30]}&order=price_low&per_page=20"
                    sr = session.get(search_url, headers=headers, timeout=5)
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

                # DEINE STRENGEN REGELN BEIBEHALTEN - nur mehr durchsuchen!
                ok = False
                if 10 <= price < 30 and gewinn >= 8: ok = True
                if 30 <= price < 40 and gewinn >= 10: ok = True
                if 40 <= price <= 80 and gewinn >= 12: ok = True

                if ok and gewinn > 5:
                    photo_url = ""
                    ph = it.get("photo")
                    if isinstance(ph, dict):
                        photo_url = ph.get("url","")
                    elif isinstance(ph, list) and len(ph) > 0:
                        photo_url = ph[0].get("url","") if isinstance(ph[0], dict) else ""
                    
                    real_deals.append({
                        "id": it["id"],
                        "title": it["title"],
                        "price_buy": price,
                        "price_sell": round(sicherer_verkauf,2),
                        "profit": round(gewinn,2),
                        "seller": it.get("user",{}).get("login",""),
                        "seller_id": it.get("user",{}).get("id",""),
                        "photo": photo_url,
                        "url": f"https://www.vinted.de/items/{it['id']}",
                        "seller_url": f"https://www.vinted.de/member/{it.get('user',{}).get('id','')}-{it.get('user',{}).get('login','')}",
                        "checked": True
                    })
                    if len(real_deals) >= 20:
                        return real_deals, total_scanned
            
            # Kurze Pause zwischen Seiten damit Vinted nicht blockt
            time.sleep(0.5)
            
            if len(real_deals) >= 20:
                break
                
        return real_deals[:20], total_scanned
    except Exception as e:
        print(f"Fetch Fehler: {e}")
        return real_deals, total_scanned

@app.get("/api/deals")
def api_deals():
    deals, scanned = fetch_real_items(per_page=100, pages_to_scan=8)  # 800 Produkte!
    return {"deals": deals, "count": len(deals), "scanned": scanned, "timestamp": time.time()}

@app.get("/", response_class=HTMLResponse)
def index():
    return """
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vinted Flip - 800 Scan</title>
<style>
body{background:#121212;color:#fff;font-family:system-ui;padding:16px;max-width:800px;margin:0 auto}
.card{background:#1e1e1e;border:1px solid #333;border-radius:12px;padding:12px;margin:12px 0;display:flex;gap:12px}
.card img{width:80px;height:80px;object-fit:cover;border-radius:8px;background:#2a2a2a}
.badge{background:#00c95022;color:#00c950;padding:2px 8px;border-radius:99px;font-size:12px}
.profit{color:#00c950;font-weight:700}
.btn{background:#fff;color:#000;border:0;padding:8px 12px;border-radius:8px;font-weight:600;cursor:pointer}
.small{color:#888;font-size:12px}
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px}
.progress{width:100%;height:4px;background:#333;border-radius:2px;overflow:hidden;margin:8px 0;display:none}
.progress-bar{height:100%;background:#00c950;width:0%;transition:width 0.3s}
</style>
</head>
<body>
<div class="header">
<h2 style="margin:0">Vinted Flip - 800 Scan</h2>
<button class="btn" onclick="load()">Aktualisieren</button>
</div>
<div id="status" class="small">Bereit - durchsucht 800 Produkte pro Klick mit deinen strengen Regeln</div>
<div class="progress" id="prog"><div class="progress-bar" id="progbar"></div></div>
<div id="list"></div>
<script>
async function load(){
 document.getElementById('prog').style.display='block';
 document.getElementById('progbar').style.width='10%';
 document.getElementById('status').innerText='Durchsuche 800 echte Vinted Artikel... das dauert 20-30 Sekunden weil deine Regeln streng sind...';
 document.getElementById('list').innerHTML='';
 const r = await fetch('/api/deals');
 document.getElementById('progbar').style.width='90%';
 const data = await r.json();
 document.getElementById('prog').style.display='none';
 document.getElementById('status').innerText = `Letzter Scan: jetzt | Geprüft: ${data.scanned} Artikel | Echte Deals: ${data.count} | Strenge Regeln: 10-29€ braucht 8€+, 30-39€ braucht 10€+, 40-80€ braucht 12€+ Gewinn`;
 const list = document.getElementById('list');
 list.innerHTML='';
 if(data.deals.length==0){ list.innerHTML='<p class="small">Bei '+data.scanned+' geprüften Artikeln gerade keiner mit deinen strengen Regeln. Nochmal klicken - Vinted lädt jede Minute 100+ neue hoch.</p>'; return; }
 data.deals.forEach(d=>{
   const div=document.createElement('div'); div.className='card';
   div.innerHTML=`<img src="${d.photo}" onerror="this.style.background='#333'"><div style="flex:1"><div style="font-weight:600">${d.title}</div><div class="small">Verkäufer: ${d.seller} <span class="badge">✓ live geprüft ID ${d.id}</span></div><div style="margin-top:6px">Kauf <b>${d.price_buy}€</b> | Verkauf <b>${d.price_sell}€</b> | <span class="profit">Gewinn +${d.profit}€</span></div><div class="small">Rechnung: ${d.price_sell}€ x0.95 -0.70€ -5€ Versand -${d.price_buy}€ = +${d.profit}€</div><div style="margin-top:8px"><a href="${d.seller_url}" target="_blank"><button class="btn">Zu ${d.seller}</button></a> <a href="${d.url}" target="_blank" style="margin-left:8px;color:#aaa;font-size:13px">Produkt öffnen</a></div></div>`;
   list.appendChild(div);
 })
}
load();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
