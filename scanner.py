"""UK resale scanner: manual-only buying; secrets via environment, no scraping."""
import csv, io, json, os, sqlite3, urllib.request, urllib.parse
from decimal import Decimal
from datetime import datetime,timezone
from urllib.parse import urlparse
from pathlib import Path

LIMITS={"capital":Decimal("100"),"reserve":Decimal("20"),"max_item":Decimal("50"),"min_profit":Decimal("15"),"min_roi":Decimal("30")}
def gbp(x):
    v=Decimal(str(x)).quantize(Decimal("0.01"))
    if not v.is_finite() or v<0: raise ValueError("Invalid monetary amount")
    return v
def score(r):
    required=("source","sku","title","category","url","purchase_price","expected_sale","fees","outbound_postage","packaging","return_reserve","recent_sales","comparable_count","condition_verified","valuation_source","valuation_checked_at")
    if any(not str(r.get(k,"")).strip() for k in required): raise ValueError("Missing evidence or field")
    for k in ("url","valuation_source"):
        u=urlparse(r[k])
        if u.scheme!="https" or not u.hostname or u.username or u.password: raise ValueError("HTTPS link required")
    stamp=datetime.fromisoformat(r["valuation_checked_at"].replace("Z","+00:00"))
    age=(datetime.now(timezone.utc)-stamp.astimezone(timezone.utc)).total_seconds()
    if stamp.tzinfo is None or age<0 or age>7*86400: raise ValueError("Stale valuation")
    buy=gbp(r["purchase_price"]); sale=gbp(r["expected_sale"])
    costs=sum((gbp(r[k]) for k in ("fees","outbound_postage","packaging","return_reserve")),Decimal(0))
    total=buy+costs
    if total<=0: raise ValueError("Zero cost")
    profit=sale-total
    roi=(profit/total*100).quantize(Decimal("0.01"))
    qualified=(buy<=LIMITS["max_item"] and buy<=LIMITS["capital"]-LIMITS["reserve"] and profit>=LIMITS["min_profit"] and roi>=LIMITS["min_roi"] and int(r["recent_sales"])>=3 and int(r["comparable_count"])>=5 and str(r["condition_verified"]).lower() in ("true","1","yes"))
    return {"source":r["source"],"sku":r["sku"],"title":r["title"],"category":r["category"],"url":r["url"],"buy":str(buy),"sale":str(sale),"profit":str(profit),"roi":str(roi),"status":"QUALIFIED" if qualified else "REVIEW"}
def connect(path):
    conn=sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS deals(id INTEGER PRIMARY KEY, key TEXT UNIQUE, detail TEXT, notified INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS commitments(id INTEGER PRIMARY KEY, deal_id INTEGER UNIQUE, amount TEXT, state TEXT)")
    conn.commit()
    return conn
def ingest(csv_text,db):
    count=0
    with connect(db) as c:
        for row in csv.DictReader(io.StringIO(csv_text)):
            result=score(row)
            key="|".join((result["source"],result["sku"],result["url"]))
            count+=c.execute("INSERT OR IGNORE INTO deals(key,detail) VALUES (?,?)",(key,json.dumps(result))).rowcount
    return count
class DenyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args): raise ValueError("Feed redirects disabled")
def fetch(db,url=None,host=None,transport=None):
    url=url or os.getenv("RESALE_APPROVED_FEED_URL","")
    host=host or os.getenv("RESALE_APPROVED_FEED_HOST","")
    p=urlparse(url)
    if not host or p.scheme!="https" or p.hostname!=host.lower() or p.username or p.password or p.port not in (None,443): raise ValueError("Feed not approved")
    req=urllib.request.Request(url,headers={"Accept":"text/csv","User-Agent":"ResaleScanner/1.0"})
    opener=transport or urllib.request.build_opener(DenyRedirect()).open
    with opener(req,timeout=15) as res:
        mime=res.headers.get("Content-Type","").split(";")[0].strip().lower()
        if mime not in ("text/csv","application/csv","application/octet-stream"): raise ValueError("Non-CSV response")
        raw=res.read(2000001)
    if len(raw)>2000000: raise ValueError("Feed too large")
    return ingest(raw.decode("utf-8-sig"),db)
def available(db):
    with connect(db) as c:
        held=sum((gbp(x[0]) for x in c.execute("SELECT amount FROM commitments WHERE state IN ('RESERVED','PURCHASED')")),Decimal(0))
    return LIMITS["capital"]-LIMITS["reserve"]-held
def prepare_checkout(db,deal_id):
    with connect(db) as c:
        c.execute("BEGIN IMMEDIATE")
        entry=c.execute("SELECT detail FROM deals WHERE id=?",(deal_id,)).fetchone()
        if not entry: raise ValueError("Deal missing")
        r=json.loads(entry[0])
        if r["status"]!="QUALIFIED" or gbp(r["buy"])>LIMITS["max_item"]: raise ValueError("Not eligible")
        if c.execute("SELECT 1 FROM commitments WHERE deal_id=?",(deal_id,)).fetchone(): raise ValueError("Already committed")
        held=sum((gbp(x[0]) for x in c.execute("SELECT amount FROM commitments WHERE state IN ('RESERVED','PURCHASED')")),Decimal(0))
        if gbp(r["buy"])>LIMITS["capital"]-LIMITS["reserve"]-held: raise ValueError("Budget exceeded")
        c.execute("INSERT INTO commitments(deal_id,amount,state) VALUES (?,?,'RESERVED')",(deal_id,r["buy"]))
    return {"manual_purchase_url":r["url"],"max_item_price":r["buy"],"instruction":"Verify item and pay manually on merchant website"}
def settle(db,deal_id,state):
    if state not in ("PURCHASED","CANCELLED"): raise ValueError("Invalid state")
    with connect(db) as c:
        if c.execute("UPDATE commitments SET state=? WHERE deal_id=? AND state='RESERVED'",(state,deal_id)).rowcount!=1: raise ValueError("No reservation")
def telegram(db,send=False,transport=None):
    with connect(db) as c:
        rows=c.execute("SELECT id,detail FROM deals WHERE notified=0").fetchall()
        out=[]
        for id,detail in rows:
            d=json.loads(detail)
            if d["status"]!="QUALIFIED":continue
            msg=f"Resale lead - verify before purchasing!\\n{d['title']}\\nBuy £{d['buy']} | Estimated profit £{d['profit']} | ROI {d['roi']}%\\n{d['url']}"
            if not send: out.append({"id":id,"status":"DRY_RUN","message":msg});continue
            token=os.environ.get("TELEGRAM_BOT_TOKEN");chat=os.environ.get("TELEGRAM_CHAT_ID")
            if not token or not chat: raise ValueError("Missing Telegram secrets")
            payload=urllib.parse.urlencode({"chat_id":chat,"text":msg}).encode()
            req=urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",data=payload,method="POST")
            with (transport or urllib.request.urlopen)(req,timeout=15) as response:
                if json.load(response).get("ok") is not True:raise ValueError("Telegram did not confirm")
            c.execute("UPDATE deals SET notified=1 WHERE id=?",(id,))
            out.append({"id":id,"status":"SENT"})
    return out
def auto_purchase(*args,**kwargs):
    raise PermissionError("Real-money auto-buy is locked: no merchant checkout integration or separate mandate")
if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("command",choices=["feed","import","list","alerts","budget","checkout","settle","autobuy"])
    p.add_argument("--db",default=os.getenv("RESALE_DB","resale.sqlite"))
    p.add_argument("--file");p.add_argument("--id",type=int);p.add_argument("--state");p.add_argument("--send",action="store_true")
    a=p.parse_args()
    if a.command=="feed": result={"imported":fetch(a.db)}
    elif a.command=="import":result={"imported":ingest(Path(a.file).read_text(),a.db)}
    elif a.command=="list":
        with connect(a.db) as c:result=[{"id":id,**json.loads(detail)} for id,detail in c.execute("SELECT id,detail FROM deals")]
    elif a.command=="alerts": result=telegram(a.db,send=a.send)
    elif a.command=="budget":result={"available":str(available(a.db))}
    elif a.command=="checkout":result=prepare_checkout(a.db,a.id)
    elif a.command=="settle":settle(a.db,a.id,a.state);result={"status":a.state}
    else:result=auto_purchase()
    print(json.dumps(result,indent=2))
