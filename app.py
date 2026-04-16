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
  body { font-family: -apple-system, Arial, sans-serif; background: #f0f7f0; color: #1a2e1a; }
  .header { padding: 20px 32px; background: #1e5c2e; display: flex; align-items: center; justify-content: space-between; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
  .header h1 { font-size: 18px; font-weight: 700; color: #fff; letter-spacing: 0.02em; }
  .header-right { display: flex; align-items: center; gap: 16px; }
  .header .live { font-size: 12px; color: #a8e6b8; display: flex; align-items: center; gap: 6px; }
  .live-dot { width: 7px; height: 7px; background: #a8e6b8; border-radius: 50%; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
  .header-date { font-size: 12px; color: #a8e6b8; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 14px; padding: 24px 32px; }
  .stat { background: #fff; border: 1px solid #c8e6c9; border-radius: 12px; padding: 16px 18px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
  .stat-label { font-size: 11px; color: #5a8a5a; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px; font-weight: 600; }
  .stat-val { font-size: 26px; font-weight: 700; color: #1a2e1a; }
  .stat-val.green { color: #2e7d32; }
  .stat-val.red   { color: #c62828; }
  .stat-val.amber { color: #e65100; }
  .section { padding: 0 32px 32px; }
  .section-title { font-size: 12px; font-weight: 700; color: #5a8a5a; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 14px; padding-bottom: 8px; border-bottom: 2px solid #c8e6c9; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
  th { text-align: left; padding: 11px 14px; font-size: 11px; color: #5a8a5a; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid #e8f5e9; background: #f5fdf5; font-weight: 700; }
  td { padding: 11px 14px; border-bottom: 1px solid #f0f9f0; color: #1a2e1a; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #f0faf0; }
  .badge { display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 700; }
  .pending { background: #e3f2fd; color: #1565c0; }
  .tp      { background: #e8f5e9; color: #2e7d32; }
  .sl      { background: #ffebee; color: #c62828; }
  .expired { background: #f5f5f5; color: #757575; }
  .buy     { color: #2e7d32; font-weight: 700; }
  .sell    { color: #c62828; font-weight: 700; }
  .t1      { background: #e8f5e9; color: #1b5e20; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }
  .t2      { background: #fff8e1; color: #e65100; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }
  .crypto  { background: #f3e5f5; color: #6a1b9a; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }
  .btn { padding: 4px 11px; border-radius: 6px; border: none; cursor: pointer; font-size: 11px; font-weight: 700; margin-right: 4px; transition: opacity 0.15s; }
  .btn:hover { opacity: 0.8; }
  .btn-tp  { background: #e8f5e9; color: #2e7d32; border: 1px solid #a5d6a7; }
  .btn-sl  { background: #ffebee; color: #c62828; border: 1px solid #ef9a9a; }
  .btn-exp { background: #f5f5f5; color: #757575; border: 1px solid #e0e0e0; }
  .pair-stats { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 14px; margin-bottom: 32px; }
  .pair-card { background: #fff; border: 1px solid #c8e6c9; border-radius: 12px; padding: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); border-left: 4px solid #2e7d32; }
  .pair-name { font-size: 15px; font-weight: 700; color: #1a2e1a; margin-bottom: 3px; }
  .pair-tf   { font-size: 11px; color: #7ab87a; margin-bottom: 12px; }
  .pair-nums { display: flex; gap: 16px; }
  .pair-num  { font-size: 11px; color: #7ab87a; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
  .pair-num span { display: block; font-size: 17px; font-weight: 700; color: #1a2e1a; margin-top: 2px; }
  .refresh { font-size: 11px; color: #a0c8a0; text-align: right; padding: 0 32px 20px; }
  .empty-state { text-align: center; padding: 48px; color: #7ab87a; font-size: 14px; }
  .empty-icon { font-size: 40px; margin-bottom: 12px; }
</style>
</head>
<body>

<div class="header">
  <h1>Cmvng Bot — Signal Dashboard</h1>
  <div class="header-right">
    <div class="header-date" id="hdate"></div>
    <div class="live"><div class="live-dot"></div> Live</div>
  </div>
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
      {% if not signals %}
    <tr><td colspan="13"><div class="empty-state"><div class="empty-icon">📡</div>No signals yet — alerts will appear here automatically</div></td></tr>
    {% endif %}
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
const d = new Date();
document.getElementById('hdate').textContent = d.toLocaleDateString('en-GB', {day:'numeric',month:'short',year:'numeric'});
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
