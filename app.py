from flask import Flask, request, jsonify, render_template_string
import requests
import json
import sqlite3
import os
from datetime import datetime, timezone

app = Flask(__name__)

# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════

TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
DB_PATH         = "signals.db"

# ═══════════════════════════════════════════════════════════
# PAIR CONFIGURATION
# Category, timeframe, risk % per trade
# ═══════════════════════════════════════════════════════════

PAIRS = {
    # Tier 1 — 0.5% risk
    "XAUUSD_30M":  {"category": "Tier 1", "risk": 0.5,  "grade": "A — Strong"},
    "EURJPY_15M":  {"category": "Tier 1", "risk": 0.5,  "grade": "A — Strong"},
    "USDJPY_15M":  {"category": "Tier 1", "risk": 0.5,  "grade": "A — Strong"},
    "EURUSD_1H":   {"category": "Tier 1", "risk": 0.5,  "grade": "A — Strong"},
    "GBPJPY_30M":  {"category": "Tier 1", "risk": 0.5,  "grade": "A — Strong"},
    "GBPNZD_30M":  {"category": "Tier 1", "risk": 0.5,  "grade": "A — Strong"},
    "EURUSD_30M":  {"category": "Tier 1", "risk": 0.5,  "grade": "A — Strong"},
    "NZDCAD_1H":   {"category": "Tier 1", "risk": 0.5,  "grade": "A — Strong"},
    # Tier 2 — 0.25% risk
    "XAGUSD_30M":  {"category": "Tier 2", "risk": 0.25, "grade": "B — Good"},
    "EURCHF_30M":  {"category": "Tier 2", "risk": 0.25, "grade": "B — Good"},
    "GBPUSD_1H":   {"category": "Tier 2", "risk": 0.25, "grade": "B — Good"},
    "USDCAD_1H":   {"category": "Tier 2", "risk": 0.25, "grade": "B — Good"},
    "EURNZD_15M":  {"category": "Tier 2", "risk": 0.25, "grade": "B — Good"},
    # Crypto — 0.1% risk
    "ADAUSD_1H":   {"category": "Crypto", "risk": 0.1,  "grade": "A — Strong"},
    "HYPEUSD_15M": {"category": "Crypto", "risk": 0.1,  "grade": "A — Strong"},
    "BNBUSD_1H":   {"category": "Crypto", "risk": 0.1,  "grade": "B — Good"},
    "BTCUSD_15M":  {"category": "Crypto", "risk": 0.1,  "grade": "A — Strong"},
    "ZECUSD_15M":  {"category": "Crypto", "risk": 0.1,  "grade": "A — Strong"},
}

# ═══════════════════════════════════════════════════════════
# DATABASE SETUP
# ═══════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS signals (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
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
            status    TEXT DEFAULT "Pending",
            fired_at  TEXT,
            closed_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ═══════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def format_signal_message(data, pair_config, rr, signal_id):
    direction = data.get("direction", "").upper()
    pair      = data.get("pair", "").upper()
    tf        = data.get("timeframe", "").upper()
    entry     = float(data.get("entry", 0))
    sl        = float(data.get("sl", 0))
    tp        = float(data.get("tp", 0))
    now       = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    emoji     = "🟢" if direction == "BUY" else "🔴"
    cat       = pair_config["category"]
    risk      = pair_config["risk"]
    grade     = pair_config["grade"]

    # Format prices based on pair type
    is_jpy    = "JPY" in pair
    is_gold   = "XAU" in pair or "XAG" in pair
    decimals  = 2 if (is_jpy or is_gold) else 5

    msg = f"""{emoji} <b>{direction} SIGNAL — {pair}</b>
──────────────────────
<b>Timeframe :</b> {tf}
<b>Entry     :</b> {entry:.{decimals}f}
<b>Stop Loss :</b> {sl:.{decimals}f}
<b>Take Profit:</b> {tp:.{decimals}f}
──────────────────────
<b>Risk      :</b> {risk}% ({cat})
<b>RR        :</b> 1 : {rr:.1f}
<b>Rating    :</b> {grade}
──────────────────────
⏰ Expires in 3 days
📅 {now}
🆔 Signal #{signal_id}"""
    return msg

# ═══════════════════════════════════════════════════════════
# WEBHOOK ENDPOINT
# TradingView sends POST to /webhook
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

        # Look up pair config
        key = f"{pair}_{timeframe}"
        pair_config = PAIRS.get(key, {
            "category": "Unknown",
            "risk": 0.1,
            "grade": "Unrated"
        })

        # Calculate RR
        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)
        rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0

        # Save to DB
        now = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        c = conn.cursor()
        c.execute('''
            INSERT INTO signals
            (pair, timeframe, direction, entry, sl, tp, rr, risk, category, grade, status, fired_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "Pending", ?)
        ''', (
            pair, timeframe, direction, entry, sl, tp, rr,
            pair_config["risk"], pair_config["category"],
            pair_config["grade"], now
        ))
        signal_id = c.lastrowid
        conn.commit()
        conn.close()

        # Send Telegram
        msg = format_signal_message(data, pair_config, rr, signal_id)
        send_telegram(msg)

        return jsonify({"status": "ok", "signal_id": signal_id}), 200

    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 500

# ═══════════════════════════════════════════════════════════
# UPDATE SIGNAL OUTCOME (TP / SL / Expired)
# ═══════════════════════════════════════════════════════════

@app.route("/update/<int:signal_id>/<status>", methods=["POST"])
def update_signal(signal_id, status):
    valid = ["TP Hit", "SL Hit", "Expired", "Pending"]
    if status not in valid:
        return jsonify({"error": "Invalid status"}), 400
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        "UPDATE signals SET status=?, closed_at=? WHERE id=?",
        (status, now, signal_id)
    )
    conn.commit()
    conn.close()

    # Send Telegram update
    emoji = "✅" if status == "TP Hit" else "❌" if status == "SL Hit" else "⏰"
    send_telegram(f"{emoji} Signal #{signal_id} — <b>{status}</b>")
    return jsonify({"status": "updated"}), 200

# ═══════════════════════════════════════════════════════════
# WEB DASHBOARD
# ═══════════════════════════════════════════════════════════

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cmvng Bot — Signal Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, Arial, sans-serif; background: #0f0f0f; color: #e0e0e0; }
  .header { padding: 24px 32px; border-bottom: 1px solid #222; display: flex; align-items: center; justify-content: space-between; }
  .header h1 { font-size: 18px; font-weight: 600; color: #fff; }
  .header .live { font-size: 12px; color: #4caf50; display: flex; align-items: center; gap: 6px; }
  .live-dot { width: 7px; height: 7px; background: #4caf50; border-radius: 50%; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 16px; padding: 24px 32px; }
  .stat { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 10px; padding: 16px; }
  .stat-label { font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
  .stat-val { font-size: 26px; font-weight: 700; color: #fff; }
  .stat-val.green { color: #4caf50; }
  .stat-val.red { color: #f44336; }
  .stat-val.amber { color: #ff9800; }
  .section { padding: 0 32px 32px; }
  .section-title { font-size: 13px; font-weight: 600; color: #888; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 14px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 10px 12px; font-size: 11px; color: #555; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid #222; }
  td { padding: 11px 12px; border-bottom: 1px solid #1a1a1a; }
  tr:hover td { background: #1a1a1a; }
  .badge { display: inline-block; padding: 2px 9px; border-radius: 20px; font-size: 11px; font-weight: 600; }
  .pending  { background: #1a2a3a; color: #64b5f6; }
  .tp       { background: #1a3a1a; color: #4caf50; }
  .sl       { background: #3a1a1a; color: #f44336; }
  .expired  { background: #2a2a2a; color: #888; }
  .buy      { color: #4caf50; font-weight: 700; }
  .sell     { color: #f44336; font-weight: 700; }
  .t1       { color: #64b5f6; font-size: 11px; }
  .t2       { color: #ff9800; font-size: 11px; }
  .crypto   { color: #ce93d8; font-size: 11px; }
  .btn { padding: 4px 10px; border-radius: 6px; border: none; cursor: pointer; font-size: 11px; font-weight: 600; margin-right: 4px; }
  .btn-tp  { background: #1a3a1a; color: #4caf50; }
  .btn-sl  { background: #3a1a1a; color: #f44336; }
  .btn-exp { background: #2a2a2a; color: #888; }
  .pair-stats { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; margin-bottom: 32px; }
  .pair-card { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 10px; padding: 14px; }
  .pair-name { font-size: 14px; font-weight: 700; color: #fff; margin-bottom: 4px; }
  .pair-tf   { font-size: 11px; color: #666; margin-bottom: 10px; }
  .pair-nums { display: flex; gap: 16px; }
  .pair-num  { font-size: 12px; color: #888; }
  .pair-num span { display: block; font-size: 16px; font-weight: 700; color: #fff; }
  .refresh { font-size: 12px; color: #555; text-align: right; padding: 0 32px 16px; }
</style>
</head>
<body>

<div class="header">
  <h1>Cmvng Bot — Signal Dashboard</h1>
  <div class="live"><div class="live-dot"></div> Live</div>
</div>

<div class="stats">
  <div class="stat"><div class="stat-label">Total Signals</div><div class="stat-val">{{ stats.total }}</div></div>
  <div class="stat"><div class="stat-label">Win Rate</div><div class="stat-val {{ 'green' if stats.wr >= 45 else 'amber' if stats.wr >= 35 else 'red' }}">{{ stats.wr }}%</div></div>
  <div class="stat"><div class="stat-label">Profit Factor</div><div class="stat-val {{ 'green' if stats.pf >= 1.4 else 'amber' if stats.pf >= 1.0 else 'red' }}">{{ stats.pf }}</div></div>
  <div class="stat"><div class="stat-label">TP Hit</div><div class="stat-val green">{{ stats.tp }}</div></div>
  <div class="stat"><div class="stat-label">SL Hit</div><div class="stat-val red">{{ stats.sl }}</div></div>
  <div class="stat"><div class="stat-label">Pending</div><div class="stat-val amber">{{ stats.pending }}</div></div>
</div>

<div class="section">
  <div class="section-title">Pair Performance</div>
  <div class="pair-stats">
    {% for p in pair_stats %}
    <div class="pair-card">
      <div class="pair-name">{{ p.pair }}</div>
      <div class="pair-tf">{{ p.timeframe }} &nbsp;·&nbsp;
        <span class="{{ 't1' if p.category == 'Tier 1' else 't2' if p.category == 'Tier 2' else 'crypto' }}">
          {{ p.category }} · {{ p.risk }}% risk
        </span>
      </div>
      <div class="pair-nums">
        <div class="pair-num">Signals<span>{{ p.total }}</span></div>
        <div class="pair-num">Win Rate<span style="color:{% if p.wr >= 45 %}#4caf50{% elif p.wr >= 35 %}#ff9800{% else %}#f44336{% endif %}">{{ p.wr }}%</span></div>
        <div class="pair-num">PF<span style="color:{% if p.pf >= 1.4 %}#4caf50{% elif p.pf >= 1.0 %}#ff9800{% else %}#f44336{% endif %}">{{ p.pf }}</span></div>
      </div>
    </div>
    {% endfor %}
  </div>
</div>

<div class="section">
  <div class="section-title">Recent Signals</div>
  <table>
    <thead>
      <tr>
        <th>#</th><th>Pair</th><th>TF</th><th>Dir</th>
        <th>Entry</th><th>SL</th><th>TP</th><th>RR</th>
        <th>Risk</th><th>Category</th><th>Status</th><th>Time</th><th>Action</th>
      </tr>
    </thead>
    <tbody>
      {% for s in signals %}
      <tr>
        <td style="color:#555">{{ s.id }}</td>
        <td style="font-weight:600;color:#fff">{{ s.pair }}</td>
        <td style="color:#666">{{ s.timeframe }}</td>
        <td class="{{ 'buy' if s.direction == 'BUY' else 'sell' }}">{{ s.direction }}</td>
        <td>{{ s.entry }}</td>
        <td style="color:#f44336">{{ s.sl }}</td>
        <td style="color:#4caf50">{{ s.tp }}</td>
        <td>1:{{ s.rr }}</td>
        <td>{{ s.risk }}%</td>
        <td class="{{ 't1' if s.category == 'Tier 1' else 't2' if s.category == 'Tier 2' else 'crypto' }}">{{ s.category }}</td>
        <td>
          <span class="badge {{ 'pending' if s.status == 'Pending' else 'tp' if s.status == 'TP Hit' else 'sl' if s.status == 'SL Hit' else 'expired' }}">
            {{ s.status }}
          </span>
        </td>
        <td style="color:#555;font-size:11px">{{ s.fired_at[:16].replace("T"," ") }}</td>
        <td>
          {% if s.status == "Pending" %}
          <button class="btn btn-tp" onclick="update({{ s.id }}, 'TP Hit')">TP</button>
          <button class="btn btn-sl" onclick="update({{ s.id }}, 'SL Hit')">SL</button>
          <button class="btn btn-exp" onclick="update({{ s.id }}, 'Expired')">Exp</button>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<div class="refresh">Auto-refreshes every 60 seconds</div>

<script>
function update(id, status) {
  fetch('/update/' + id + '/' + encodeURIComponent(status), {method:'POST'})
    .then(() => location.reload());
}
setTimeout(() => location.reload(), 60000);
</script>
</body>
</html>
"""

@app.route("/")
def dashboard():
    conn = get_db()

    # Overall stats
    signals = conn.execute("SELECT * FROM signals ORDER BY id DESC").fetchall()
    total   = len(signals)
    tp      = sum(1 for s in signals if s["status"] == "TP Hit")
    sl      = sum(1 for s in signals if s["status"] == "SL Hit")
    pending = sum(1 for s in signals if s["status"] == "Pending")
    closed  = tp + sl
    wr      = round((tp / closed * 100), 1) if closed > 0 else 0
    pf      = round((tp * 1.5) / sl, 2) if sl > 0 else 0

    stats = {"total": total, "tp": tp, "sl": sl, "pending": pending, "wr": wr, "pf": pf}

    # Per pair stats
    pairs_raw = conn.execute("""
        SELECT pair, timeframe, category, risk,
               COUNT(*) as total,
               SUM(CASE WHEN status='TP Hit' THEN 1 ELSE 0 END) as tp,
               SUM(CASE WHEN status='SL Hit' THEN 1 ELSE 0 END) as sl
        FROM signals GROUP BY pair, timeframe
        ORDER BY total DESC
    """).fetchall()

    pair_stats = []
    for p in pairs_raw:
        closed_p = p["tp"] + p["sl"]
        wr_p = round(p["tp"] / closed_p * 100, 1) if closed_p > 0 else 0
        pf_p = round((p["tp"] * 1.5) / p["sl"], 2) if p["sl"] > 0 else 0
        pair_stats.append({
            "pair": p["pair"], "timeframe": p["timeframe"],
            "category": p["category"], "risk": p["risk"],
            "total": p["total"], "wr": wr_p, "pf": pf_p
        })

    conn.close()
    return render_template_string(DASHBOARD_HTML, signals=signals, stats=stats, pair_stats=pair_stats)

# ═══════════════════════════════════════════════════════════
# TEST ENDPOINT — confirm server is running
# ═══════════════════════════════════════════════════════════

@app.route("/test", methods=["GET"])
def test():
    send_telegram("✅ <b>Cmvng Bot is live and running!</b>\n\nSignals will appear here automatically.")
    return jsonify({"status": "ok", "message": "Telegram test sent"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
