"""FastAPI app: WhatsApp Cloud API webhook (signed, de-duplicated, rate-limited, processed in the background),
a chat endpoint, a WhatsApp-style demo page, and the admin dashboard."""
import hashlib
import hmac
import logging
import os

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from typing import Optional

from . import db, kv, tools
from .agent import BRAIN, handle_message, handle_media, notify_customer

log = logging.getLogger("wa-agent")
db.init()
app = FastAPI(title="Bilingual WhatsApp Business Agent")

VERIFY_TOKEN = os.environ.get("WA_VERIFY_TOKEN", "dev-verify-token")
WA_TOKEN = os.environ.get("WA_ACCESS_TOKEN", "")
WA_PHONE_ID = os.environ.get("WA_PHONE_NUMBER_ID", "")
WA_APP_SECRET = os.environ.get("WA_APP_SECRET", "")  # Meta app secret: enables X-Hub-Signature-256 verification
DEMO_MODE = os.environ.get("DEMO_MODE", "") == "1"
RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MIN", "20"))
GRAPH = os.environ.get("WA_GRAPH_URL", "https://graph.facebook.com/v21.0")
# Price per million tokens, used for the cost column when an LLM brain is active (override via env)
PRICE_IN = float(os.environ.get("PRICE_IN_PER_MTOK", "0.15"))
PRICE_OUT = float(os.environ.get("PRICE_OUT_PER_MTOK", "0.6"))
NON_TEXT = "Sorry, I can only read text messages for now. / Maazrat, abhi main sirf text message samajh sakta hoon."


def answer(customer: str, text: str) -> str:
    """One customer message in, one reply out. Serialised per customer; never raises."""
    with kv.customer_lock(customer) as got:
        if not got:
            return ""
        return handle_message(customer, text)


def answer_media(customer: str, kind: str, media_id: str = "") -> str:
    with kv.customer_lock(customer) as got:
        if not got:
            return ""
        return handle_media(customer, kind, media_id)


class Chat(BaseModel):
    customer: str = "demo-customer"
    text: str = ""
    media: Optional[str] = None  # "image" simulates a customer sending a payment screenshot


@app.get("/health")
def health():
    return {"ok": True, "brain": BRAIN.name, "db": "postgres" if db.PG else "sqlite", "kv": kv.backend()}


@app.post("/chat")
def chat(m: Chat):
    if not kv.allow(m.customer, RATE_LIMIT):
        raise HTTPException(429, "Too many messages, slow down a little.")
    if m.media:
        return {"reply": answer_media(m.customer, m.media, "demo-proof"), "brain": BRAIN.name}
    return {"reply": answer(m.customer, m.text), "brain": BRAIN.name}


@app.get("/webhook")
def verify(mode: str = Query(None, alias="hub.mode"), token: str = Query(None, alias="hub.verify_token"),
           challenge: str = Query(None, alias="hub.challenge")):
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge or "")
    return PlainTextResponse("forbidden", status_code=403)


def _signature_ok(raw: bytes, header: str) -> bool:
    if not WA_APP_SECRET:
        return True  # dev: unsigned. In production always set WA_APP_SECRET.
    expected = "sha256=" + hmac.new(WA_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


def send_whatsapp(to: str, text: str):
    if not (WA_TOKEN and WA_PHONE_ID):
        log.info("WhatsApp send skipped (no credentials): to=%s", to)
        return
    try:
        r = httpx.post(f"{GRAPH}/{WA_PHONE_ID}/messages", headers={"Authorization": f"Bearer {WA_TOKEN}"}, timeout=20,
                       json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text, "preview_url": False}})
        if r.status_code >= 300:
            log.error("WhatsApp send failed %s: %s", r.status_code, r.text[:300])
    except Exception:
        log.exception("WhatsApp send error")


def mark_read(message_id: str):
    if not (WA_TOKEN and WA_PHONE_ID and message_id):
        return
    try:
        httpx.post(f"{GRAPH}/{WA_PHONE_ID}/messages", headers={"Authorization": f"Bearer {WA_TOKEN}"}, timeout=10,
                   json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id})
    except Exception:
        pass


def process(sender: str, text: str, message_id: str):
    mark_read(message_id)
    try:
        reply = answer(sender, text)
    except Exception:
        log.exception("agent error")
        reply = "Sorry, something went wrong on our side. A team member will reply shortly."
    if reply:
        send_whatsapp(sender, reply)


def process_media(sender: str, kind: str, media_id: str, message_id: str):
    mark_read(message_id)
    try:
        reply = answer_media(sender, kind, media_id)
    except Exception:
        log.exception("media error")
        reply = "Sorry, something went wrong on our side. A team member will reply shortly."
    if reply:
        send_whatsapp(sender, reply)


@app.post("/webhook")
async def webhook(req: Request, bg: BackgroundTasks):
    raw = await req.body()
    if not _signature_ok(raw, req.headers.get("x-hub-signature-256", "")):
        raise HTTPException(403, "bad signature")
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(400, "bad json")
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):  # delivery/read "statuses" are ignored
                mid, sender = msg.get("id", ""), msg.get("from", "")
                if not sender or not kv.first_time(mid):
                    continue  # redelivery of a message we already handled
                kind = msg.get("type")
                if kind in ("image", "document"):  # payment screenshots / receipts
                    if kv.allow(sender, RATE_LIMIT):
                        bg.add_task(process_media, sender, kind, (msg.get(kind) or {}).get("id", ""), mid)
                    continue
                if kind != "text":
                    bg.add_task(send_whatsapp, sender, NON_TEXT)
                    continue
                if not kv.allow(sender, RATE_LIMIT):
                    log.warning("rate limited %s", sender)
                    continue
                bg.add_task(process, sender, msg["text"]["body"], mid)
    return {"ok": True}  # answer Meta immediately; the reply is sent from the background task


@app.get("/demo", response_class=HTMLResponse)
def demo_page():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "demo.html"))


@app.post("/demo/reset")
def demo_reset():
    if not DEMO_MODE:
        raise HTTPException(403, "reset is only available when DEMO_MODE=1")
    db.init(reset=True)
    return {"ok": True}



ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


class StatusChange(BaseModel):
    status: str


@app.post("/api/orders/{order_id}/status")
def set_order_status(order_id: int, body: StatusChange, req: Request):
    """The shop moves an order along: confirmed > preparing > ready > out_for_delivery > delivered (or cancelled)."""
    from .shop import STATUSES
    if ADMIN_TOKEN and req.headers.get("x-admin-token") != ADMIN_TOKEN:
        raise HTTPException(401, "admin token required")
    if body.status not in STATUSES:
        raise HTTPException(400, f"status must be one of {STATUSES}")
    import time as _t
    c = db.conn()
    r = c.execute("SELECT id FROM orders WHERE id=?", (order_id,)).fetchone()
    if not r:
        c.close()
        raise HTTPException(404, "order not found")
    c.execute("UPDATE orders SET status=?, updated=? WHERE id=?", (body.status, _t.time(), order_id))
    c.commit()
    c.close()
    return {"ok": True, "order_id": order_id, "status": body.status}



class PayAction(BaseModel):
    action: str                     # "confirm" or "reject"
    amount: Optional[int] = None    # amount actually received (defaults to the amount due)
    reason: str = ""


@app.post("/api/orders/{order_id}/payment")
def payment_action(order_id: int, body: PayAction, req: Request):
    """A team member verifies (or rejects) a payment. Confirming moves the order to 'confirmed' and messages the customer."""
    if ADMIN_TOKEN and req.headers.get("x-admin-token") != ADMIN_TOKEN:
        raise HTTPException(401, "admin token required")
    if body.action == "confirm":
        r = tools.team_confirm_payment(order_id, body.amount)
        if r.get("ok"):
            o = r["order"]
            text = notify_customer(r["customer"], "payment_confirmed", order_id=o["order_id"], summary=o["summary"],
                                   ready_at_text=o.get("ready_at_text", ""), team_phone=o["team_phone"])
            send_whatsapp(r["customer"], text)
    elif body.action == "reject":
        r = tools.team_reject_payment(order_id, body.reason)
        if r.get("ok"):
            o = r["order"]
            text = notify_customer(r["customer"], "payment_rejected", order_id=o["order_id"], team_phone=o["team_phone"],
                                   reason_part=f" ({body.reason})" if body.reason else "")
            send_whatsapp(r["customer"], text)
    else:
        raise HTTPException(400, "action must be 'confirm' or 'reject'")
    if not r.get("ok"):
        raise HTTPException(409, r.get("error", "failed"))
    return {"ok": True, "order_id": order_id, "status": r["order"]["status"]}


@app.get("/api/team-messages")
def team_messages(customer: str, after: int = 0):
    """Messages the team/system sent proactively (payment verified, ...). The demo page polls this."""
    c = db.conn()
    rows = [dict(r) for r in c.execute("SELECT id, text FROM messages WHERE customer=? AND role='team' AND id>? ORDER BY id", (customer, after))]
    c.close()
    return rows


def _orders(c):
    import json
    out = []
    for r in c.execute("SELECT o.*, p.name AS pname FROM orders o JOIN products p ON p.id=o.product_id ORDER BY o.id DESC LIMIT 10"):
        try:
            its = json.loads(r["items"]) if r["items"] else []
        except Exception:
            its = []
        name = ", ".join(f"{i['qty']} x {i['name']}" for i in its) if its else f"{r['qty']} x {r['pname']}"
        out.append({"id": r["id"], "customer": r["customer"], "name": name, "qty": r["delivery"] or "pickup", "total": r["total"],
                    "status": r["status"], "payment_mode": r["payment_mode"] or "cod", "due_now": r["due_now"] or 0,
                    "paid_amount": r["paid_amount"] or 0, "payment_ref": r["payment_ref"] or ""})
    return out


@app.get("/api/stats")
def stats():
    c = db.conn()
    one = lambda q: c.execute(q).fetchone()[0] or 0
    convs = one("SELECT COUNT(DISTINCT customer) FROM messages")
    tin, tout = one("SELECT SUM(tokens_in) FROM messages"), one("SELECT SUM(tokens_out) FROM messages")
    cost = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT
    out = {
        "brain": BRAIN.name,
        "conversations": convs,
        "messages": one("SELECT COUNT(*) FROM messages WHERE role='user'"),
        "orders": one("SELECT COUNT(*) FROM orders"),
        "revenue": one("SELECT SUM(total) FROM orders"),
        "escalations": one("SELECT COUNT(*) FROM escalations WHERE resolved=0"),
        "avg_latency_ms": round(one("SELECT AVG(latency_ms) FROM messages WHERE role='assistant'")),
        "cost_usd": round(cost, 4),
        "cost_per_conversation_usd": round(cost / convs, 5) if convs else 0,
        "recent_orders": _orders(c),
        "recent_chats": [dict(r) for r in c.execute("SELECT customer, role, text FROM messages ORDER BY id DESC LIMIT 16")],
        "open_escalations": [dict(r) for r in c.execute("SELECT customer, reason FROM escalations WHERE resolved=0 ORDER BY id DESC LIMIT 10")],
        "stock": [dict(r) for r in c.execute(
            "SELECT p.name || ' - ' || v.name AS name, v.stock, v.price FROM variations v JOIN products p ON p.id=v.product_id ORDER BY v.id")],
    }
    c.close()
    return out


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASH


DASH = """<!doctype html><meta charset=utf8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Agent Admin</title>
<style>
body{font:15px system-ui;margin:0;background:#f5f6f8;color:#161a23}
main{max-width:980px;margin:0 auto;padding:24px 16px;display:grid;gap:16px}
h1{margin:0;font-size:22px}.k{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}
.c{background:#fff;border:1px solid #dde0e7;border-radius:8px;padding:12px}.c b{display:block;font-size:22px}
.g{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:700px){.g{grid-template-columns:1fr}}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:5px 6px;border-bottom:1px solid #eceef2;text-align:left}
.u{color:#2a4bd7}.a{color:#1b7a4f}.chat div{padding:3px 0;border-bottom:1px solid #eceef2;font-size:13px}
form{display:flex;gap:6px}input{flex:1;padding:8px;border:1px solid #c9ced8;border-radius:6px}button{padding:8px 14px}
</style>
<main><h1>Sweet Crumbs · Agent Admin</h1><div class=k id=k></div>
<div class=c><b style="font-size:14px">Try the agent</b><div class=chat id=sim style="margin:8px 0"></div>
<form id=f><input id=t placeholder="e.g.  2 chocolate cake chahiye" autocomplete=off><button>Send</button></form></div>
<div class=g><div class=c><b style="font-size:14px">Orders</b><table id=o></table></div>
<div class=c><b style="font-size:14px">Stock</b><table id=s></table><br><b style="font-size:14px">Needs a human</b><table id=e></table></div></div>
<div class=c><b style="font-size:14px">Recent messages</b><div class=chat id=m></div></div></main>
<script>
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function payCell(r){
 if(r.status==='payment_submitted')return `<b>Verify Rs ${r.due_now}</b><br><small>${esc(r.payment_ref)}</small><br><button onclick="payAct(${r.id},'confirm')">Confirm payment</button> <button onclick="payAct(${r.id},'reject')">Reject</button>`;
 if(r.status==='awaiting_payment')return `waiting for Rs ${r.due_now}`;
 if(r.paid_amount>0)return `paid Rs ${r.paid_amount}`;
 return r.payment_mode==='cod'?'cash on delivery/pickup':esc(r.payment_mode)}
async function payAct(id,action){let reason='';if(action==='reject'){reason=prompt('Why? (shown to the customer)')||''}
 const r=await fetch('/api/orders/'+id+'/payment',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({action,reason})});
 if(!r.ok)alert('Failed: '+await r.text());load()}
async function setStatus(id,st){await fetch('/api/orders/'+id+'/status',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({status:st})});load()}
async function load(){const d=await (await fetch('/api/stats')).json();
k.innerHTML=[['Conversations',d.conversations],['Orders',d.orders],['Revenue','Rs '+d.revenue],['Open handoffs',d.escalations],['Avg latency',d.avg_latency_ms+' ms'],['Cost / conv','$'+d.cost_per_conversation_usd],['Brain',d.brain]].map(x=>`<div class=c>${x[0]}<b>${esc(x[1])}</b></div>`).join('');
const ST=['confirmed','preparing','ready','out_for_delivery','delivered','cancelled'];o.innerHTML='<tr><th>#<th>Customer<th>Items<th>Type<th>Total<th>Status<th>Payment</tr>'+d.recent_orders.map(r=>`<tr><td>${r.id}<td>${esc(r.customer)}<td>${esc(r.name)}<td>${esc(r.qty)}<td>${r.total}<td><select onchange="setStatus(${r.id},this.value)">${ST.map(x=>`<option${x==r.status?' selected':''}>${x}</option>`).join('')}</select><td>${payCell(r)}</tr>`).join('');
s.innerHTML=d.stock.map(r=>`<tr><td>${esc(r.name)}<td>${r.stock}<td>Rs ${r.price}</tr>`).join('');
e.innerHTML=d.open_escalations.map(r=>`<tr><td>${esc(r.customer)}<td>${esc(r.reason)}</tr>`).join('')||'<tr><td>None</tr>';
m.innerHTML=d.recent_chats.map(r=>`<div><span class=${r.role[0]}>${r.role=='user'?esc(r.customer):'agent'}</span>: ${esc(r.text)}</div>`).join('')}
f.onsubmit=async ev=>{ev.preventDefault();const text=t.value;if(!text)return;t.value='';
sim.insertAdjacentHTML('beforeend',`<div class=u>you: ${esc(text)}</div>`);
const r=await (await fetch('/chat',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({customer:'web-demo',text})})).json();
sim.insertAdjacentHTML('beforeend',`<div class=a>agent: ${esc(r.reply)}</div>`);load()};
load();setInterval(load,5000);
</script>"""
