from flask import Flask, request, jsonify, render_template_string
import requests
import pg8000.native
import os
import threading
import time
from datetime import datetime, timezone, timedelta

app = Flask(__name__)

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TWELVEDATA_KEY   = os.environ.get("TWELVEDATA_KEY", "")
DATABASE_URL     = os.environ.get("DATABASE_URL", "")
EXPIRY_DAYS      = 3
CHECK_INTERVAL   = 900

PAIRS = {
    "XAUUSD_30M":  {"category": "Tier 1", "risk": 0.5,  "grade": "A - Strong"},
    "EURJPY_15M":  {"category": "Tier 1", "risk": 0.5,  "grade": "A - Strong"},
    "USDJPY_15M":  {"category": "Tier 1", "risk": 0.5,  "grade": "A - Strong"},
    "EURUSD_1H":   {"category": "Tier 1", "risk": 0.5,  "grade": "A - Strong"},
    "GBPJPY_30M":  {"category": "Tier 1", "risk": 0.5,  "grade": "A - Strong"},
    "GBPNZD_30M":  {"category": "Tier 1", "risk": 0.5,  "grade": "A - Strong"},
    "EURUSD_30M":  {"category": "Tier 1", "risk": 0.5,  "grade": "A - Strong"},
    "NZDCAD_1H":   {"category": "Tier 1", "risk": 0.5,  "grade": "A - Strong"},
    "XAGUSD_30M":  {"category": "Tier 2", "risk": 0.25, "grade": "B - Good"},
    "EURCHF_30M":  {"category": "Tier 2", "risk": 0.25, "grade": "B - Good"},
    "GBPUSD_1H":   {"category": "Tier 2", "risk": 0.25, "grade": "B - Good"},
    "USDCAD_1H":   {"category": "Tier 2", "risk": 0.25, "grade": "B - Good"},
    "EURNZD_15M":  {"category": "Tier 2", "risk": 0.25, "grade": "B - Good"},
    "ADAUSD_1H":   {"category": "Crypto", "risk": 0.1,  "grade": "A - Strong"},
    "HYPEUSD_15M": {"category": "Crypto", "risk": 0.1,  "grade": "A - Strong"},
    "BNBUSD_1H":   {"category": "Crypto", "risk": 0.1,  "grade": "B - Good"},
    "BTCUSD_15M":  {"category": "Crypto", "risk": 0.1,  "grade": "A - Strong"},
    "ZECUSD_15M":  {"category": "Crypto", "risk": 0.1,  "grade": "A - Strong"},
}

SYMBOL_MAP = {
    "XAUUSD": "XAU/USD", "EURJPY": "EUR/JPY", "USDJPY": "USD/JPY",
    "EURUSD": "EUR/USD", "GBPJPY": "GBP/JPY", "EURCHF": "EUR/CHF",
    "GBPUSD": "GBP/USD", "EURNZD": "EUR/NZD", "ADAUSD": "ADA/USD",
    "BTCUSD": "BTC/USD", "XAGUSD": "XAG/USD", "NZDCAD": "NZD/CAD",
    "GBPNZD": "GBP/NZD", "USDCAD": "USD/CAD", "GBPCAD": "GBP/CAD",
    "CADJPY": "CAD/JPY", "AUDUSD": "AUD/USD", "EURCAD": "EUR/CAD",
    "BNBUSD": "BNB/USD", "ZECUSD": "ZEC/USD", "SOLUSD": "SOL/USD",
    "ETHUSD": "ETH/USD", "XRPUSD": "XRP/USD", "HYPEUSD": "HYPE/USD",
}

# ═══════════════════════════════════════════════════════════
# DATABASE — PostgreSQL (persists across redeploys)
# ═══════════════════════════════════════════════════════════

def get_db():
    import urllib.parse
    # Railway provides DATABASE_URL as postgresql://user:pass@host:port/db
    # or postgres://user:pass@host:port/db
    db_url = DATABASE_URL.replace('postgres://', 'postgresql://')
    url = urllib.parse.urlparse(db_url)
    user = url.username
    password = url.password
    host = url.hostname
    port = url.port or 5432
    database = url.path.lstrip('/')
    print('DB connecting: host={} port={} db={} user={}'.format(host, port, database, user))
    conn = pg8000.native.Connection(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        ssl_context=True
    )
    return conn

def init_db():
    conn = get_db()
    conn.run("""
        CREATE TABLE IF NOT EXISTS signals (
            id        SERIAL PRIMARY KEY,
            pair      TEXT,
            timeframe TEXT,
            direction TEXT,
            entry     REAL,
            sl        REAL,
            tp        REAL,
            rr        REAL,
            risk      REAL,
            category  TEXT,
            grade     TEXT,
            status    TEXT DEFAULT 'Pending',
            filled    BOOLEAN DEFAULT FALSE,
            fired_at  TEXT,
            closed_at TEXT
        )
    """)
    try:
        conn.run("ALTER TABLE signals ADD COLUMN IF NOT EXISTS filled BOOLEAN DEFAULT FALSE")
    except Exception:
        pass
    conn.close()

# ═══════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            "https://api.telegram.org/bot{}/sendMessage".format(TELEGRAM_TOKEN),
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        print("Telegram error: {}".format(e))

# ═══════════════════════════════════════════════════════════
# TWELVEDATA — batch price fetch (1 API call for all pairs)
# ═══════════════════════════════════════════════════════════

def get_prices_batch(pairs):
    symbols = []
    pair_to_symbol = {}
    for pair in pairs:
        symbol = SYMBOL_MAP.get(pair.upper())
        if symbol:
            symbols.append(symbol)
            pair_to_symbol[pair.upper()] = symbol
    if not symbols or not TWELVEDATA_KEY:
        return {}
    try:
        symbol_str = ",".join(symbols)
        r = requests.get(
            "https://api.twelvedata.com/price?symbol={}&apikey={}".format(symbol_str, TWELVEDATA_KEY),
            timeout=15
        )
        data = r.json()
        prices = {}
        for pair in pairs:
            symbol = pair_to_symbol.get(pair.upper())
            if not symbol:
                continue
            if symbol in data and "price" in data[symbol]:
                prices[pair.upper()] = float(data[symbol]["price"])
            elif "price" in data and len(symbols) == 1:
                prices[pair.upper()] = float(data["price"])
        return prices
    except Exception as e:
        print("TwelveData batch error: {}".format(e))
        return {}

# ═══════════════════════════════════════════════════════════
# AUTO UPDATE SIGNAL STATUS
# ═══════════════════════════════════════════════════════════

def update_signal_auto(sig_id, status, pair, direction, price=None, tp=None, sl=None):
    conn = get_db()
    conn.run("UPDATE signals SET status=:s, closed_at=:c WHERE id=:i",
             s=status, c=datetime.now(timezone.utc).isoformat(), i=sig_id)
    conn.close()
    if status == "TP Hit":
        msg = "✅ <b>TP HIT — {} {}</b>\nPrice: {} | TP: {}\n🆔 Signal #{}".format(pair, direction, price, tp, sig_id)
    elif status == "SL Hit":
        msg = "❌ <b>SL HIT — {} {}</b>\nPrice: {} | SL: {}\n🆔 Signal #{}".format(pair, direction, price, sl, sig_id)
    else:
        msg = "⏰ <b>EXPIRED — {} {}</b> (3 days)\n🆔 Signal #{}".format(pair, direction, sig_id)
    send_telegram(msg)
    print("Signal #{} -> {}".format(sig_id, status))

# ═══════════════════════════════════════════════════════════
# BACKGROUND MONITOR — checks every 15 minutes
# Uses 1 batch API call for ALL pending pairs
# Only checks Pending signals — resolved ones never rescanned
# ═══════════════════════════════════════════════════════════

def check_pending_signals():
    while True:
        try:
            conn = get_db()
            rows = conn.run("SELECT * FROM signals WHERE status = 'Pending'")
            cols = [c['name'] for c in conn.columns]
            pending = [dict(zip(cols, r)) for r in rows]
            conn.close()

            if not pending:
                time.sleep(CHECK_INTERVAL)
                continue

            unique_pairs = list(set(s["pair"] for s in pending))
            prices = get_prices_batch(unique_pairs)
            print("Monitor: {} pairs, {} pending signals".format(len(unique_pairs), len(pending)))

            for s in pending:
                try:
                    fired_dt = datetime.fromisoformat(s["fired_at"])
                    if fired_dt.tzinfo is None:
                        fired_dt = fired_dt.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) - fired_dt > timedelta(days=EXPIRY_DAYS):
                        update_signal_auto(s["id"], "Expired", s["pair"], s["direction"])
                        continue
                    price = prices.get(s["pair"].upper())
                    if price is None:
                        continue

                    filled = s.get("filled", False)

                    # Step 1 — check if entry has been filled first
                    if not filled:
                        if s["direction"] == "BUY" and price <= s["entry"]:
                            conn2 = get_db()
                            conn2.run("UPDATE signals SET filled=TRUE WHERE id=:i", i=s["id"])
                            conn2.close()
                            filled = True
                            print("Signal #{} FILLED at {}".format(s["id"], price))
                        elif s["direction"] == "SELL" and price >= s["entry"]:
                            conn2 = get_db()
                            conn2.run("UPDATE signals SET filled=TRUE WHERE id=:i", i=s["id"])
                            conn2.close()
                            filled = True
                            print("Signal #{} FILLED at {}".format(s["id"], price))

                    # Step 2 — only check TP/SL if entry was filled
                    if filled:
                        if s["direction"] == "BUY":
                            if price >= s["tp"]:
                                update_signal_auto(s["id"], "TP Hit", s["pair"], s["direction"], price, s["tp"], s["sl"])
                            elif price <= s["sl"]:
                                update_signal_auto(s["id"], "SL Hit", s["pair"], s["direction"], price, s["tp"], s["sl"])
                        elif s["direction"] == "SELL":
                            if price <= s["tp"]:
                                update_signal_auto(s["id"], "TP Hit", s["pair"], s["direction"], price, s["tp"], s["sl"])
                            elif price >= s["sl"]:
                                update_signal_auto(s["id"], "SL Hit", s["pair"], s["direction"], price, s["tp"], s["sl"])
                except Exception as e:
                    print("Signal #{} error: {}".format(s["id"], e))
        except Exception as e:
            print("Monitor error: {}".format(e))
        time.sleep(CHECK_INTERVAL)

# ═══════════════════════════════════════════════════════════
# WEBHOOK — receives TradingView alerts
# ═══════════════════════════════════════════════════════════

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "No data"}), 400
        pair      = data.get("pair", "").upper()
        timeframe = data.get("timeframe", "").upper()
        direction = data.get("direction", "").upper()
        entry     = float(data.get("entry", 0))
        sl        = float(data.get("sl", 0))
        tp        = float(data.get("tp", 0))
        key       = "{}_{}".format(pair, timeframe)
        cfg       = PAIRS.get(key, {"category": "Unknown", "risk": 0.1, "grade": "Unrated"})
        sl_dist   = abs(entry - sl)
        tp_dist   = abs(tp - entry)
        rr        = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0
        now       = datetime.now(timezone.utc).isoformat()

        conn = get_db()
        result = conn.run(
            """INSERT INTO signals
            (pair,timeframe,direction,entry,sl,tp,rr,risk,category,grade,status,fired_at)
            VALUES (:pair,:tf,:dir,:entry,:sl,:tp,:rr,:risk,:cat,:grade,'Pending',:now) RETURNING id""",
            pair=pair, tf=timeframe, dir=direction, entry=entry,
            sl=sl, tp=tp, rr=rr, risk=cfg["risk"],
            cat=cfg["category"], grade=cfg["grade"], now=now
        )
        signal_id = result[0][0]
        conn.close()

        is_jpy   = "JPY" in pair
        is_metal = "XAU" in pair or "XAG" in pair
        dec      = 2 if (is_jpy or is_metal) else 5
        emoji    = "🟢" if direction == "BUY" else "🔴"
        ts       = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
        msg = (
            "{} <b>{} SIGNAL — {}</b>\n"
            "──────────────────────\n"
            "<b>Timeframe :</b> {}\n"
            "<b>Entry     :</b> {:.{}f}\n"
            "<b>Stop Loss :</b> {:.{}f}\n"
            "<b>Take Profit:</b> {:.{}f}\n"
            "──────────────────────\n"
            "<b>Risk      :</b> {}% ({})\n"
            "<b>RR        :</b> 1 : {:.1f}\n"
            "<b>Rating    :</b> {}\n"
            "──────────────────────\n"
            "⏰ Expires in 3 days\n"
            "📅 {}\n"
            "🆔 Signal #{}"
        ).format(emoji, direction, pair, timeframe,
                 entry, dec, sl, dec, tp, dec,
                 cfg["risk"], cfg["category"], rr, cfg["grade"], ts, signal_id)
        send_telegram(msg)
        return jsonify({"status": "ok", "signal_id": signal_id}), 200
    except Exception as e:
        print("Webhook error: {}".format(e))
        return jsonify({"error": str(e)}), 500

# ═══════════════════════════════════════════════════════════
# MANUAL UPDATE
# ═══════════════════════════════════════════════════════════

@app.route("/update/<int:signal_id>/<status>", methods=["POST"])
def update_signal(signal_id, status):
    if status not in ["TP Hit", "SL Hit", "Expired", "Pending"]:
        return jsonify({"error": "Invalid status"}), 400
    conn = get_db()
    conn.run("UPDATE signals SET status=:s, closed_at=:c WHERE id=:i",
             s=status, c=datetime.now(timezone.utc).isoformat(), i=signal_id)
    conn.close()
    emoji = "✅" if status == "TP Hit" else "❌" if status == "SL Hit" else "⏰"
    send_telegram("{} Signal #{} — <b>{}</b>".format(emoji, signal_id, status))
    return jsonify({"status": "updated"}), 200

# ═══════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════

DASHBOARD_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cmvng Bot</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,Arial,sans-serif;background:#f0f7f0;color:#1a2e1a}
.hdr{padding:20px 32px;background:#1e5c2e;display:flex;align-items:center;justify-content:space-between}
.hdr h1{font-size:18px;font-weight:700;color:#fff}
.hdr-r{display:flex;align-items:center;gap:14px}
.live{font-size:12px;color:#a8e6b8;display:flex;align-items:center;gap:5px}
.dot{width:7px;height:7px;background:#a8e6b8;border-radius:50%;animation:p 2s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.4}}
.hdate{font-size:12px;color:#a8e6b8}
.mbadge{background:#a8e6b8;color:#1e5c2e;font-size:11px;padding:3px 10px;border-radius:20px;font-weight:700}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;padding:24px 32px}
.stat{background:#fff;border:1px solid #c8e6c9;border-radius:12px;padding:14px 16px}
.slbl{font-size:10px;color:#5a8a5a;text-transform:uppercase;letter-spacing:.06em;margin-bottom:5px;font-weight:700}
.sval{font-size:24px;font-weight:700;color:#1a2e1a}
.sval.g{color:#2e7d32}.sval.r{color:#c62828}.sval.a{color:#e65100}
.sec{padding:0 32px 32px}
.stit{font-size:11px;font-weight:700;color:#5a8a5a;text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px;padding-bottom:7px;border-bottom:2px solid #c8e6c9}
.pgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-bottom:28px}
.pc{background:#fff;border:1px solid #c8e6c9;border-radius:12px;padding:14px;border-left:4px solid #2e7d32}
.pn{font-size:14px;font-weight:700;margin-bottom:2px}
.pt{font-size:11px;color:#7ab87a;margin-bottom:10px}
.pnums{display:flex;gap:14px}
.pnum{font-size:10px;color:#7ab87a;font-weight:700;text-transform:uppercase}
.pnum span{display:block;font-size:15px;font-weight:700;color:#1a2e1a;margin-top:1px}
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px;background:#fff;border-radius:12px;overflow:hidden;min-width:680px}
th{text-align:left;padding:10px 12px;font-size:10px;color:#5a8a5a;text-transform:uppercase;border-bottom:1px solid #e8f5e9;background:#f5fdf5;font-weight:700}
td{padding:10px 12px;border-bottom:1px solid #f0f9f0}
tr:last-child td{border-bottom:none}
tr:hover td{background:#f0faf0}
.bdg{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700}
.pnd{background:#e3f2fd;color:#1565c0}
.tph{background:#e8f5e9;color:#2e7d32}
.slh{background:#ffebee;color:#c62828}
.exp{background:#f5f5f5;color:#757575}
.buy{color:#2e7d32;font-weight:700}.sell{color:#c62828;font-weight:700}
.t1{background:#e8f5e9;color:#1b5e20;padding:2px 7px;border-radius:20px;font-size:10px;font-weight:700}
.t2{background:#fff8e1;color:#e65100;padding:2px 7px;border-radius:20px;font-size:10px;font-weight:700}
.cry{background:#f3e5f5;color:#6a1b9a;padding:2px 7px;border-radius:20px;font-size:10px;font-weight:700}
.btn{padding:3px 9px;border-radius:5px;border:1px solid;cursor:pointer;font-size:10px;font-weight:700;margin-right:3px}
.btp{background:#e8f5e9;color:#2e7d32;border-color:#a5d6a7}
.bsl{background:#ffebee;color:#c62828;border-color:#ef9a9a}
.bex{background:#f5f5f5;color:#757575;border-color:#e0e0e0}
.empty{text-align:center;padding:40px;color:#7ab87a}
.ref{font-size:11px;color:#a0c8a0;text-align:right;padding:0 32px 16px}
</style></head><body>
<div class="hdr">
  <h1>Cmvng Bot — Signal Dashboard</h1>
  <div class="hdr-r">
    <span class="mbadge">Auto-monitoring ON</span>
    <span class="hdate" id="hd"></span>
    <div class="live"><div class="dot"></div> Live</div>
  </div>
</div>
<div class="stats">
  <div class="stat"><div class="slbl">Total</div><div class="sval">{{ stats.total }}</div></div>
  <div class="stat"><div class="slbl">Win Rate</div><div class="sval {{ 'g' if stats.wr >= 45 else 'a' if stats.wr >= 35 else 'r' }}">{{ stats.wr }}%</div></div>
  <div class="stat"><div class="slbl">Prof Factor</div><div class="sval {{ 'g' if stats.pf >= 1.4 else 'a' if stats.pf >= 1.0 else 'r' }}">{{ stats.pf }}</div></div>
  <div class="stat"><div class="slbl">TP Hit</div><div class="sval g">{{ stats.tp }}</div></div>
  <div class="stat"><div class="slbl">SL Hit</div><div class="sval r">{{ stats.sl }}</div></div>
  <div class="stat"><div class="slbl">Pending</div><div class="sval a">{{ stats.pending }}</div></div>
</div>
{% if pair_stats %}
<div class="sec">
  <div class="stit">Pair Performance</div>
  <div class="pgrid">
    {% for p in pair_stats %}
    <div class="pc">
      <div class="pn">{{ p.pair }}</div>
      <div class="pt">{{ p.timeframe }} &nbsp;·&nbsp;
        <span class="{{ 't1' if p.category == 'Tier 1' else 't2' if p.category == 'Tier 2' else 'cry' }}">{{ p.category }}</span>
      </div>
      <div class="pnums">
        <div class="pnum">Signals<span>{{ p.total }}</span></div>
        <div class="pnum">Wins<span style="color:#2e7d32">{{ p.tp }}</span></div>
        <div class="pnum">Losses<span style="color:#c62828">{{ p.sl }}</span></div>
      </div>
    </div>
    {% endfor %}
  </div>
</div>
{% endif %}
<div class="sec">
  <div class="stit">Signal Log</div>
  <div class="tw">
    <table>
      <thead><tr><th>#</th><th>Pair</th><th>TF</th><th>Dir</th><th>Entry</th><th>SL</th><th>TP</th><th>RR</th><th>Risk</th><th>Cat</th><th>Filled</th><th>Status</th><th>Time</th><th>Action</th></tr></thead>
      <tbody>
        {% if not signals %}<tr><td colspan="13"><div class="empty">📡 No signals yet — waiting for alerts</div></td></tr>{% endif %}
        {% for s in signals %}
        <tr>
          <td style="color:#aaa">{{ s.id }}</td>
          <td style="font-weight:700">{{ s.pair }}</td>
          <td style="color:#888">{{ s.timeframe }}</td>
          <td class="{{ 'buy' if s.direction == 'BUY' else 'sell' }}">{{ s.direction }}</td>
          <td>{{ s.entry }}</td>
          <td style="color:#c62828">{{ s.sl }}</td>
          <td style="color:#2e7d32">{{ s.tp }}</td>
          <td>1:{{ s.rr }}</td>
          <td>{{ s.risk }}%</td>
          <td><span class="{{ 't1' if s.category == 'Tier 1' else 't2' if s.category == 'Tier 2' else 'cry' }}">{{ s.category }}</span></td>
          <td style="font-size:11px">{{ "✅" if s.filled else "⏳" }}</td>
          <td><span class="bdg {{ 'pnd' if s.status == 'Pending' else 'tph' if s.status == 'TP Hit' else 'slh' if s.status == 'SL Hit' else 'exp' }}">{{ s.status }}</span></td>
          <td style="color:#aaa;font-size:11px">{{ s.fired_at[:16].replace("T"," ") if s.fired_at else "" }}</td>
          <td>
            {% if s.status == "Pending" %}
            <button class="btn btp" onclick="upd({{ s.id }},'TP Hit')">TP</button>
            <button class="btn bsl" onclick="upd({{ s.id }},'SL Hit')">SL</button>
            <button class="btn bex" onclick="upd({{ s.id }},'Expired')">Exp</button>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
<div class="ref">Auto-refreshes every 60s &nbsp;·&nbsp; Prices checked every 15 mins &nbsp;·&nbsp; Data stored in PostgreSQL</div>
<script>
function upd(id,s){fetch('/update/'+id+'/'+encodeURIComponent(s),{method:'POST'}).then(()=>location.reload())}
setTimeout(()=>location.reload(),60000);
document.getElementById('hd').textContent=new Date().toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'});
</script>
</body></html>"""

@app.route("/add", methods=["POST"])
def add_signal():
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "No data"}), 400
        pair      = data.get("pair", "").upper()
        timeframe = data.get("timeframe", "").upper()
        direction = data.get("direction", "").upper()
        entry     = float(data.get("entry", 0))
        sl        = float(data.get("sl", 0))
        tp        = float(data.get("tp", 0))
        fired_at  = data.get("fired_at", datetime.now(timezone.utc).isoformat())
        key       = "{}_{}".format(pair, timeframe)
        cfg       = PAIRS.get(key, {"category": "Unknown", "risk": 0.1, "grade": "Unrated"})
        sl_dist   = abs(entry - sl)
        tp_dist   = abs(tp - entry)
        rr        = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0
        conn = get_db()
        result = conn.run(
            """INSERT INTO signals
            (pair,timeframe,direction,entry,sl,tp,rr,risk,category,grade,status,fired_at)
            VALUES (:pair,:tf,:dir,:entry,:sl,:tp,:rr,:risk,:cat,:grade,'Pending',:now) RETURNING id""",
            pair=pair, tf=timeframe, dir=direction, entry=entry,
            sl=sl, tp=tp, rr=rr, risk=cfg["risk"],
            cat=cfg["category"], grade=cfg["grade"], now=fired_at
        )
        signal_id = result[0][0]
        conn.close()
        return jsonify({"status": "ok", "signal_id": signal_id, "message": "Signal added — monitor will check it within 15 mins"}), 200
    except Exception as e:
        print("Add signal error: {}".format(e))
        return jsonify({"error": str(e)}), 500

@app.route("/")
def dashboard():
    conn = get_db()
    rows = conn.run("SELECT * FROM signals ORDER BY id DESC")
    cols = [c['name'] for c in conn.columns]
    signals = [dict(zip(cols, r)) for r in rows]
    prows = conn.run("""
        SELECT pair, timeframe, category, risk,
               COUNT(*) as total,
               SUM(CASE WHEN status='TP Hit' THEN 1 ELSE 0 END) as tp,
               SUM(CASE WHEN status='SL Hit' THEN 1 ELSE 0 END) as sl
        FROM signals GROUP BY pair, timeframe, category, risk ORDER BY total DESC
    """)
    pcols = [c['name'] for c in conn.columns]
    pair_stats = [dict(zip(pcols, r)) for r in prows]
    conn.close()
    total   = len(signals)
    tp      = sum(1 for s in signals if s["status"] == "TP Hit")
    sl      = sum(1 for s in signals if s["status"] == "SL Hit")
    pending = sum(1 for s in signals if s["status"] == "Pending")
    closed  = tp + sl
    wr      = round(tp / closed * 100, 1) if closed > 0 else 0
    pf      = round((tp * 1.5) / sl, 2) if sl > 0 else 0
    stats   = {"total": total, "tp": tp, "sl": sl, "pending": pending, "wr": wr, "pf": pf}
    return render_template_string(DASHBOARD_HTML, signals=signals, stats=stats, pair_stats=pair_stats)

@app.route("/test")
def test():
    send_telegram("✅ <b>Cmvng Bot is live!</b>\n\nPostgreSQL connected — data persists forever.\nAuto price monitoring active via TwelveData.")
    return jsonify({"status": "ok"}), 200

# ═══════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════

try:
    init_db()
    print("Database initialized OK")
except Exception as e:
    print("DB init error: {}".format(e))
    print("DATABASE_URL set: {}".format(bool(DATABASE_URL)))

monitor_thread = threading.Thread(target=check_pending_signals, daemon=True)
monitor_thread.start()
print("Cmvng Bot started — monitor running")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
