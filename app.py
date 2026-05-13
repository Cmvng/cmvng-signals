from flask import Flask, request, jsonify, render_template_string
import requests
import pg8000.native
import os
import threading
import time
import urllib.parse
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
    "MNTUSD_15M":  {"category": "Crypto", "risk": 0.1,  "grade": "A - Strong"},
    "MNTUSD_1H":   {"category": "Crypto", "risk": 0.1,  "grade": "A - Strong"},
    "CADJPY_30M":  {"category": "Tier 2", "risk": 0.25, "grade": "B - Good"},
    "EURCAD_30M":  {"category": "Tier 2", "risk": 0.25, "grade": "B - Good"},
    "GBPCAD_15M":  {"category": "Tier 2", "risk": 0.25, "grade": "B - Good"},
    "GBPAUD_15M":  {"category": "Tier 2", "risk": 0.25, "grade": "B - Good"},
    "SOLUSD_15M":  {"category": "Crypto", "risk": 0.1,  "grade": "B - Good"},
    "ETHUSD_15M":  {"category": "Crypto", "risk": 0.1,  "grade": "B - Good"},
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
    "MNTUSD": "MNT/USD",
}

BINANCE_MAP = {
    "MNTUSD": "MNTUSDT", "ADAUSD": "ADAUSDT", "BTCUSD": "BTCUSDT",
    "ETHUSD": "ETHUSDT", "BNBUSD": "BNBUSDT", "SOLUSD": "SOLUSDT",
    "XRPUSD": "XRPUSDT", "ZECUSD": "ZECUSDT", "HYPEUSD": "HYPEUSDT",
}

def get_binance_price(pair):
    symbol = BINANCE_MAP.get(pair.upper())
    if not symbol:
        return None
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol={}".format(symbol), timeout=10)
        data = r.json()
        if "price" in data:
            return float(data["price"])
    except Exception as e:
        print("Binance error {}: {}".format(pair, e))
    return None

# ═══════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════

def get_db():
    db_url = DATABASE_URL.replace('postgres://', 'postgresql://')
    url = urllib.parse.urlparse(db_url)
    conn = pg8000.native.Connection(
        host=url.hostname, port=url.port or 5432,
        database=url.path.lstrip('/'),
        user=url.username, password=url.password,
        ssl_context=True
    )
    return conn

def init_db():
    conn = get_db()
    conn.run("""CREATE TABLE IF NOT EXISTS signals (
        id SERIAL PRIMARY KEY, pair TEXT, timeframe TEXT, direction TEXT,
        entry REAL, sl REAL, tp REAL, rr REAL, risk REAL,
        category TEXT, grade TEXT, status TEXT DEFAULT 'Pending',
        filled BOOLEAN DEFAULT FALSE, fired_at TEXT, closed_at TEXT
    )""")
    try:
        conn.run("ALTER TABLE signals ADD COLUMN IF NOT EXISTS filled BOOLEAN DEFAULT FALSE")
    except Exception:
        pass
    try:
        conn.run("ALTER TABLE signals ADD COLUMN IF NOT EXISTS filled_at TEXT")
    except Exception:
        pass
    conn.close()

# ═══════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            "https://api.telegram.org/bot{}/sendMessage".format(TELEGRAM_TOKEN),
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10
        )
    except Exception as e:
        print("Telegram error: {}".format(e))

# ═══════════════════════════════════════
# PRICE FETCH — TwelveData batch + Binance fallback
# ═══════════════════════════════════════

# Yahoo Finance symbol map — no API key, no rate limits
YAHOO_MAP = {
    "XAUUSD": "GC=F", "XAGUSD": "SI=F",
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X",
    "EURJPY": "EURJPY=X", "GBPJPY": "GBPJPY=X", "EURCHF": "EURCHF=X",
    "NZDCAD": "NZDCAD=X", "USDCAD": "USDCAD=X", "EURNZD": "EURNZD=X",
    "GBPNZD": "GBPNZD=X", "GBPCAD": "GBPCAD=X", "GBPAUD": "GBPAUD=X",
    "CADJPY": "CADJPY=X", "EURCAD": "EURCAD=X", "AUDUSD": "AUDUSD=X",
    "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD", "SOLUSD": "SOL-USD",
    "ADAUSD": "ADA-USD", "BNBUSD": "BNB-USD", "ZECUSD": "ZEC-USD",
}

def get_yahoo_price(pair):
    symbol = YAHOO_MAP.get(pair.upper())
    if not symbol:
        return None
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/{}?interval=1m&range=1d".format(symbol)
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
        price = meta.get("regularMarketPrice", None)
        if price:
            return float(price)
    except Exception:
        pass
    return None

def get_prices_batch(pairs):
    prices = {}
    # Primary: Yahoo Finance (no rate limits, covers forex + crypto)
    for pair in pairs:
        p = get_yahoo_price(pair)
        if p is not None:
            prices[pair.upper()] = p

    # Fallback: Binance for any crypto not found on Yahoo
    for pair in pairs:
        if pair.upper() not in prices and pair.upper() in BINANCE_MAP:
            bp = get_binance_price(pair)
            if bp is not None:
                prices[pair.upper()] = bp

    # Last resort: TwelveData for anything still missing (max 8 symbols)
    missing = [p for p in pairs if p.upper() not in prices]
    if missing and TWELVEDATA_KEY:
        try:
            syms = []
            p2s = {}
            for pair in missing[:8]:
                sym = SYMBOL_MAP.get(pair.upper())
                if sym:
                    syms.append(sym)
                    p2s[pair.upper()] = sym
            if syms:
                r = requests.get("https://api.twelvedata.com/price?symbol={}&apikey={}".format(",".join(syms), TWELVEDATA_KEY), timeout=10)
                data = r.json()
                for pair in missing:
                    sym = p2s.get(pair.upper())
                    if sym and sym in data and "price" in data[sym]:
                        prices[pair.upper()] = float(data[sym]["price"])
                    elif sym and "price" in data and len(syms) == 1:
                        prices[pair.upper()] = float(data["price"])
        except Exception as e:
            print("TwelveData fallback error: {}".format(e))

    missing = [p for p in pairs if p.upper() not in prices]
    if missing:
        print("Prices missing for: {}".format(", ".join(missing)))
    if prices:
        print("Prices fetched: {} of {} pairs".format(len(prices), len(pairs)))
    else:
        print("WARNING: No prices returned!")
    return prices

# ═══════════════════════════════════════
# AUTO UPDATE
# ═══════════════════════════════════════

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

# ═══════════════════════════════════════
# BACKGROUND MONITOR
# ═══════════════════════════════════════

def get_candle_data(pair, after_timestamp=None):
    """Fetch candle high/low data ONLY from after the signal fired"""
    # Yahoo Finance handles ALL pairs — forex and crypto
    yahoo_sym = YAHOO_MAP.get(pair.upper())
    if yahoo_sym:
        try:
            url = "https://query1.finance.yahoo.com/v8/finance/chart/{}?interval=5m&range=5d".format(yahoo_sym)
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=10)
            data = r.json()
            result = data.get("chart", {}).get("result", [{}])[0]
            timestamps = result.get("timestamp", [])
            indicators = result.get("indicators", {}).get("quote", [{}])[0]
            highs_raw = indicators.get("high", [])
            lows_raw = indicators.get("low", [])
            meta = result.get("meta", {})
            last_price = float(meta.get("regularMarketPrice", 0))

            if after_timestamp and timestamps:
                cutoff = int(after_timestamp.timestamp())
                filtered_highs = []
                filtered_lows = []
                for i, ts in enumerate(timestamps):
                    if ts >= cutoff:
                        if i < len(highs_raw) and highs_raw[i] is not None:
                            filtered_highs.append(highs_raw[i])
                        if i < len(lows_raw) and lows_raw[i] is not None:
                            filtered_lows.append(lows_raw[i])
                if filtered_highs and filtered_lows:
                    return {"high": max(filtered_highs), "low": min(filtered_lows), "price": last_price,
                            "highs": filtered_highs, "lows": filtered_lows}
            else:
                highs = [h for h in highs_raw if h is not None]
                lows = [l for l in lows_raw if l is not None]
                if highs and lows and last_price:
                    return {"high": max(highs), "low": min(lows), "price": last_price, "highs": highs, "lows": lows}
        except Exception as e:
            print("Yahoo candle error {}: {}".format(pair, e))

    # Binance fallback — only if Yahoo failed and pair is crypto
    if pair.upper() in BINANCE_MAP:
        try:
            symbol = BINANCE_MAP[pair.upper()]
            params = "symbol={}&interval=5m&limit=100".format(symbol)
            if after_timestamp:
                start_ms = int(after_timestamp.timestamp() * 1000)
                params += "&startTime={}".format(start_ms)
            url = "https://api.binance.com/api/v3/klines?{}".format(params)
            r = requests.get(url, timeout=5)
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                highs = [float(c[2]) for c in data]
                lows = [float(c[3]) for c in data]
                last_price = float(data[-1][4])
                return {"high": max(highs), "low": min(lows), "price": last_price, "highs": highs, "lows": lows}
            else:
                print("Binance returned non-list for {}: {}".format(pair, str(data)[:100]))
        except Exception as e:
            print("Binance fallback failed {}: {}".format(pair, str(e)[:80]))

    return None

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
            print("Monitor: {} pairs, {} pending".format(len(unique_pairs), len(pending)))

            for s in pending:
                try:
                    # Check expiry first
                    fired_dt = datetime.fromisoformat(s["fired_at"])
                    if fired_dt.tzinfo is None:
                        fired_dt = fired_dt.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) - fired_dt > timedelta(days=EXPIRY_DAYS):
                        update_signal_auto(s["id"], "Expired", s["pair"], s["direction"])
                        continue

                    # Get candle data ONLY from after this signal fired
                    # If already filled, get data from after fill time
                    filled = s.get("filled", False)
                    if filled and s.get("filled_at"):
                        check_from = datetime.fromisoformat(s["filled_at"])
                        if check_from.tzinfo is None:
                            check_from = check_from.replace(tzinfo=timezone.utc)
                    else:
                        check_from = fired_dt

                    candles = get_candle_data(s["pair"], after_timestamp=check_from)
                    if candles is None:
                        continue

                    post_signal_high = candles["high"]
                    post_signal_low = candles["low"]
                    price = candles["price"]

                    # Step 1: Check if entry was reached using candle lows/highs
                    if not filled:
                        # Sanity check — if price data is wildly different from entry, skip
                        # This catches wrong Yahoo tokens (e.g. MNT returning wrong coin)
                        if abs(price - s["entry"]) / s["entry"] > 0.9:
                            print("Price sanity FAIL for {} #{}: price={} entry={} — skipping".format(
                                s["pair"], s["id"], price, s["entry"]))
                            continue

                        entry_reached = False
                        if s["direction"] == "BUY" and post_signal_low <= s["entry"]:
                            entry_reached = True
                        elif s["direction"] == "SELL" and post_signal_high >= s["entry"]:
                            entry_reached = True

                        if entry_reached:
                            fill_time = datetime.now(timezone.utc).isoformat()
                            conn2 = get_db()
                            conn2.run("UPDATE signals SET filled=TRUE, filled_at=:t WHERE id=:i", t=fill_time, i=s["id"])
                            conn2.close()
                            send_telegram("📥 <b>ENTRY FILLED — {} {}</b>\nEntry: {}\nTime: {}\n🆔 Signal #{}".format(
                                s["pair"], s["direction"], s["entry"], fill_time[:16].replace("T"," "), s["id"]))
                            print("Signal #{} {} FILLED — entry {} reached (day low={}, day high={})".format(
                                s["id"], s["pair"], s["entry"], post_signal_low, post_signal_high))
                            continue

                    # Step 2: Only check TP/SL if entry was PREVIOUSLY filled
                    if filled:
                        if s["direction"] == "BUY":
                            # For BUY: check if post_signal_low hit SL or post_signal_high hit TP
                            sl_hit = post_signal_low <= s["sl"]
                            tp_hit = post_signal_high >= s["tp"]

                            if sl_hit and tp_hit:
                                # Both levels were touched — need candle-by-candle check
                                # Check each candle to see which was hit first
                                hit_sl_first = False
                                hit_tp_first = False
                                for i in range(len(candles["lows"])):
                                    c_low = candles["lows"][i]
                                    c_high = candles["highs"][i]
                                    if c_low <= s["sl"] and not hit_tp_first:
                                        hit_sl_first = True
                                        break
                                    if c_high >= s["tp"] and not hit_sl_first:
                                        hit_tp_first = True
                                        break
                                if hit_sl_first:
                                    update_signal_auto(s["id"], "SL Hit", s["pair"], s["direction"], price, s["tp"], s["sl"])
                                elif hit_tp_first:
                                    update_signal_auto(s["id"], "TP Hit", s["pair"], s["direction"], price, s["tp"], s["sl"])
                                else:
                                    update_signal_auto(s["id"], "SL Hit", s["pair"], s["direction"], price, s["tp"], s["sl"])
                            elif tp_hit:
                                update_signal_auto(s["id"], "TP Hit", s["pair"], s["direction"], price, s["tp"], s["sl"])
                            elif sl_hit:
                                update_signal_auto(s["id"], "SL Hit", s["pair"], s["direction"], price, s["tp"], s["sl"])

                        elif s["direction"] == "SELL":
                            # For SELL: check if post_signal_high hit SL or post_signal_low hit TP
                            sl_hit = post_signal_high >= s["sl"]
                            tp_hit = post_signal_low <= s["tp"]

                            if sl_hit and tp_hit:
                                hit_sl_first = False
                                hit_tp_first = False
                                for i in range(len(candles["highs"])):
                                    c_low = candles["lows"][i]
                                    c_high = candles["highs"][i]
                                    if c_high >= s["sl"] and not hit_tp_first:
                                        hit_sl_first = True
                                        break
                                    if c_low <= s["tp"] and not hit_sl_first:
                                        hit_tp_first = True
                                        break
                                if hit_sl_first:
                                    update_signal_auto(s["id"], "SL Hit", s["pair"], s["direction"], price, s["tp"], s["sl"])
                                elif hit_tp_first:
                                    update_signal_auto(s["id"], "TP Hit", s["pair"], s["direction"], price, s["tp"], s["sl"])
                                else:
                                    update_signal_auto(s["id"], "SL Hit", s["pair"], s["direction"], price, s["tp"], s["sl"])
                            elif tp_hit:
                                update_signal_auto(s["id"], "TP Hit", s["pair"], s["direction"], price, s["tp"], s["sl"])
                            elif sl_hit:
                                update_signal_auto(s["id"], "SL Hit", s["pair"], s["direction"], price, s["tp"], s["sl"])

                except Exception as e:
                    print("Signal #{} error: {}".format(s["id"], e))
        except Exception as e:
            print("Monitor error: {}".format(e))
        time.sleep(CHECK_INTERVAL)

# ═══════════════════════════════════════
# WEBHOOK
# ═══════════════════════════════════════

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

        # Duplicate prevention — block same pair+TF+direction within 1 hour
        try:
            conn_dup = get_db()
            recent = conn_dup.run(
                "SELECT id FROM signals WHERE pair=:p AND timeframe=:t AND direction=:d AND fired_at > :cutoff ORDER BY id DESC LIMIT 1",
                p=pair, t=timeframe, d=direction,
                cutoff=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            )
            conn_dup.close()
            if len(recent) > 0:
                print("Duplicate blocked: {} {} {} (recent signal #{} exists)".format(pair, timeframe, direction, recent[0][0]))
                return jsonify({"status": "duplicate", "message": "Signal already exists within 1 hour"}), 200
        except Exception as dup_err:
            print("Duplicate check error: {}".format(dup_err))

        key       = "{}_{}".format(pair, timeframe)
        cfg       = PAIRS.get(key, {"category": "Unknown", "risk": 0.1, "grade": "Unrated"})
        sl_dist   = abs(entry - sl)
        tp_dist   = abs(tp - entry)
        rr        = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0
        now       = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        result = conn.run(
            """INSERT INTO signals (pair,timeframe,direction,entry,sl,tp,rr,risk,category,grade,status,fired_at)
            VALUES (:pair,:tf,:dir,:entry,:sl,:tp,:rr,:risk,:cat,:grade,'Pending',:now) RETURNING id""",
            pair=pair, tf=timeframe, dir=direction, entry=entry,
            sl=sl, tp=tp, rr=rr, risk=cfg["risk"],
            cat=cfg["category"], grade=cfg["grade"], now=now)
        signal_id = result[0][0]
        conn.close()
        is_jpy   = "JPY" in pair
        is_metal = "XAU" in pair or "XAG" in pair
        dec      = 2 if (is_jpy or is_metal) else 5
        emoji    = "🟢" if direction == "BUY" else "🔴"
        ts       = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
        msg = ("{} <b>{} SIGNAL — {}</b>\n──────────────────────\n"
               "<b>Timeframe :</b> {}\n<b>Entry     :</b> {:.{}f}\n"
               "<b>Stop Loss :</b> {:.{}f}\n<b>Take Profit:</b> {:.{}f}\n"
               "──────────────────────\n<b>Risk      :</b> {}% ({})\n"
               "<b>RR        :</b> 1 : {:.1f}\n<b>Rating    :</b> {}\n"
               "──────────────────────\n⏰ Expires in 3 days\n📅 {}\n🆔 Signal #{}"
               ).format(emoji, direction, pair, timeframe,
                        entry, dec, sl, dec, tp, dec,
                        cfg["risk"], cfg["category"], rr, cfg["grade"], ts, signal_id)
        send_telegram(msg)
        return jsonify({"status": "ok", "signal_id": signal_id}), 200
    except Exception as e:
        print("Webhook error: {}".format(e))
        return jsonify({"error": str(e)}), 500

# ═══════════════════════════════════════
# ADD SIGNAL (manual)
# ═══════════════════════════════════════

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
            """INSERT INTO signals (pair,timeframe,direction,entry,sl,tp,rr,risk,category,grade,status,fired_at)
            VALUES (:pair,:tf,:dir,:entry,:sl,:tp,:rr,:risk,:cat,:grade,'Pending',:now) RETURNING id""",
            pair=pair, tf=timeframe, dir=direction, entry=entry,
            sl=sl, tp=tp, rr=rr, risk=cfg["risk"],
            cat=cfg["category"], grade=cfg["grade"], now=fired_at)
        signal_id = result[0][0]
        conn.close()
        return jsonify({"status": "ok", "signal_id": signal_id}), 200
    except Exception as e:
        print("Add error: {}".format(e))
        return jsonify({"error": str(e)}), 500

# ═══════════════════════════════════════
# FIX SIGNALS (reset false SL hits)
# ═══════════════════════════════════════

@app.route("/fix/<int:signal_id>", methods=["POST"])
def fix_signal(signal_id):
    conn = get_db()
    conn.run("UPDATE signals SET status='Pending', filled=FALSE, closed_at=NULL WHERE id=:i", i=signal_id)
    conn.close()
    return jsonify({"status": "reset", "signal_id": signal_id}), 200

# ═══════════════════════════════════════
# LANDING PAGE
# ═══════════════════════════════════════

LANDING_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cmvng Bot — Automated Trading Signals</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#f4f9f4;--card:#fff;--green-1:#0d4a1a;--green-2:#1a6b2c;--green-3:#2e8b42;--green-4:#4caf50;--green-5:#81c784;--green-6:#c8e6c9;--green-7:#e8f5e9;--text:#0d2b0d;--text2:#3a6b3a;--text3:#6b9a6b;--border:#d4e8d4;--white:#ffffff;--accent:#ff6b35}
body{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--text);overflow-x:hidden}
.nav{padding:16px 40px;display:flex;justify-content:space-between;align-items:center;max-width:1200px;margin:0 auto}
.nav-logo{font-size:20px;font-weight:700;color:var(--green-1);letter-spacing:-0.5px}
.nav-logo span{color:var(--green-4)}
.nav-link{font-size:14px;font-weight:500;color:var(--green-2);text-decoration:none;padding:8px 20px;border:1.5px solid var(--green-4);border-radius:24px;transition:all .2s}
.nav-link:hover{background:var(--green-4);color:#fff}
.hero{text-align:center;padding:80px 40px 60px;max-width:900px;margin:0 auto}
.hero-badge{display:inline-block;padding:6px 16px;background:var(--green-7);color:var(--green-2);font-size:13px;font-weight:600;border-radius:20px;margin-bottom:24px;border:1px solid var(--green-6)}
.hero h1{font-size:clamp(36px,5vw,56px);font-weight:700;color:var(--green-1);line-height:1.1;margin-bottom:20px;letter-spacing:-1.5px}
.hero h1 em{font-style:normal;color:var(--green-3)}
.hero p{font-size:18px;color:var(--text2);line-height:1.6;max-width:600px;margin:0 auto 40px}
.chart-box{max-width:900px;margin:0 auto 80px;padding:0 40px}
.chart-wrap{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:32px;position:relative;overflow:hidden}
.chart-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.chart-pair{font-size:18px;font-weight:700;color:var(--green-1)}
.chart-live{font-size:12px;color:var(--green-4);display:flex;align-items:center;gap:6px}
.chart-dot{width:6px;height:6px;background:var(--green-4);border-radius:50%;animation:blink 2s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.chart-svg{width:100%;height:200px}
.chart-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-top:24px;padding-top:20px;border-top:1px solid var(--border)}
.cs{text-align:center}
.cs-val{font-size:22px;font-weight:700;color:var(--green-1)}
.cs-val.up{color:var(--green-3)}
.cs-lbl{font-size:11px;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-top:4px;font-weight:600}
.features{max-width:1000px;margin:0 auto 80px;padding:0 40px}
.features-title{text-align:center;font-size:28px;font-weight:700;color:var(--green-1);margin-bottom:40px;letter-spacing:-0.5px}
.fgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}
.fcard{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:28px;transition:transform .15s,box-shadow .15s}
.fcard:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,80,0,.08)}
.fcard-icon{width:44px;height:44px;background:var(--green-7);border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:20px;margin-bottom:16px}
.fcard h3{font-size:16px;font-weight:700;color:var(--green-1);margin-bottom:8px}
.fcard p{font-size:14px;color:var(--text2);line-height:1.5}
.pairs-section{max-width:1000px;margin:0 auto 80px;padding:0 40px}
.pairs-title{text-align:center;font-size:28px;font-weight:700;color:var(--green-1);margin-bottom:12px}
.pairs-sub{text-align:center;font-size:15px;color:var(--text2);margin-bottom:36px}
.pgrid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px}
.ptag{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 8px;text-align:center;font-size:13px;font-weight:600;color:var(--green-1);transition:all .15s}
.ptag:hover{border-color:var(--green-4);background:var(--green-7)}
.ptag span{display:block;font-size:10px;color:var(--text3);font-weight:500;margin-top:3px}
.footer{text-align:center;padding:40px;color:var(--text3);font-size:13px;border-top:1px solid var(--border)}
</style></head><body>
<nav class="nav">
  <div class="nav-logo">cmvng<span>bot</span></div>
  <a href="/dashboard" class="nav-link">Dashboard →</a>
</nav>
<div class="hero">
  <div class="hero-badge">Automated Forex & Crypto Signals</div>
  <h1>Precision entries.<br><em>Consistent results.</em></h1>
  <p>AI-powered trading signals across 25+ forex and crypto pairs. Fibonacci-based entries, automated TP/SL monitoring, and real-time Telegram alerts — all running 24/7.</p>
</div>
<div class="chart-box">
  <div class="chart-wrap">
    <div class="chart-header">
      <div class="chart-pair">Signal Performance</div>
      <div class="chart-live"><div class="chart-dot"></div> Live monitoring</div>
    </div>
    <svg class="chart-svg" viewBox="0 0 800 200" fill="none">
      <defs>
        <linearGradient id="g1" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#4caf50" stop-opacity="0.3"/>
          <stop offset="100%" stop-color="#4caf50" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <path d="M0,160 C50,150 80,140 120,120 C160,100 200,130 240,110 C280,90 320,70 360,80 C400,90 440,60 480,50 C520,40 560,70 600,45 C640,20 680,35 720,25 C760,15 790,20 800,18" stroke="#4caf50" stroke-width="2.5" fill="none"/>
      <path d="M0,160 C50,150 80,140 120,120 C160,100 200,130 240,110 C280,90 320,70 360,80 C400,90 440,60 480,50 C520,40 560,70 600,45 C640,20 680,35 720,25 C760,15 790,20 800,18 L800,200 L0,200 Z" fill="url(#g1)"/>
      <line x1="0" y1="100" x2="800" y2="100" stroke="#e0e0e0" stroke-width="0.5" stroke-dasharray="6,4"/>
    </svg>
    <div class="chart-stats" id="landing-stats">
      <div class="cs"><div class="cs-val" id="ls-total">—</div><div class="cs-lbl">Total Signals</div></div>
      <div class="cs"><div class="cs-val up" id="ls-wr">—</div><div class="cs-lbl">Win Rate</div></div>
      <div class="cs"><div class="cs-val up" id="ls-pf">—</div><div class="cs-lbl">Profit Factor</div></div>
      <div class="cs"><div class="cs-val" id="ls-pairs">25+</div><div class="cs-lbl">Active Pairs</div></div>
    </div>
  </div>
</div>
<div class="features">
  <div class="features-title">How it works</div>
  <div class="fgrid">
    <div class="fcard"><div class="fcard-icon">📡</div><h3>Signal Detection</h3><p>Custom strategy runs on TradingView across 25+ pairs on 15M, 30M and 1H timeframes. When conditions align, a signal fires automatically.</p></div>
    <div class="fcard"><div class="fcard-icon">📱</div><h3>Instant Alerts</h3><p>Telegram notification arrives within seconds — showing exact entry, stop loss, take profit, risk percentage and reward ratio.</p></div>
    <div class="fcard"><div class="fcard-icon">🎯</div><h3>Fibonacci Entry</h3><p>Entries at the 0.5 Fibonacci retracement level — giving you the optimal entry with a proven 1:2+ risk-reward ratio on every trade.</p></div>
    <div class="fcard"><div class="fcard-icon">🤖</div><h3>Auto Monitoring</h3><p>Price checked every 15 minutes. TP hit, SL hit or expired — the bot detects it automatically and sends you the result. Fully hands-free.</p></div>
    <div class="fcard"><div class="fcard-icon">📊</div><h3>Risk Management</h3><p>Three-tier risk allocation. Tier 1 pairs get 0.5%, Tier 2 gets 0.25%, Crypto gets 0.1%. Maximum exposure controlled at all times.</p></div>
    <div class="fcard"><div class="fcard-icon">🔒</div><h3>Entry Verification</h3><p>Smart fill detection ensures TP/SL only trigger after your entry level is actually reached. No false results on unfilled orders.</p></div>
  </div>
</div>
<div class="pairs-section">
  <div class="pairs-title">Active Pairs</div>
  <div class="pairs-sub">Signals running 24/7 across forex and crypto markets</div>
  <div class="pgrid2">
    <div class="ptag">XAUUSD<span>30M · Tier 1</span></div>
    <div class="ptag">EURJPY<span>15M · Tier 1</span></div>
    <div class="ptag">USDJPY<span>15M · Tier 1</span></div>
    <div class="ptag">EURUSD<span>1H · Tier 1</span></div>
    <div class="ptag">GBPJPY<span>30M · Tier 1</span></div>
    <div class="ptag">GBPNZD<span>30M · Tier 1</span></div>
    <div class="ptag">EURUSD<span>30M · Tier 1</span></div>
    <div class="ptag">NZDCAD<span>1H · Tier 1</span></div>
    <div class="ptag">XAGUSD<span>30M · Tier 2</span></div>
    <div class="ptag">EURCHF<span>30M · Tier 2</span></div>
    <div class="ptag">GBPUSD<span>1H · Tier 2</span></div>
    <div class="ptag">USDCAD<span>1H · Tier 2</span></div>
    <div class="ptag">EURNZD<span>15M · Tier 2</span></div>
    <div class="ptag">GBPCAD<span>15M · Tier 2</span></div>
    <div class="ptag">GBPAUD<span>15M · Tier 2</span></div>
    <div class="ptag">CADJPY<span>30M · Tier 2</span></div>
    <div class="ptag">EURCAD<span>30M · Tier 2</span></div>
    <div class="ptag">ADAUSD<span>1H · Crypto</span></div>
    <div class="ptag">BTCUSD<span>15M · Crypto</span></div>
    <div class="ptag">ETHUSD<span>15M · Crypto</span></div>
    <div class="ptag">BNBUSD<span>1H · Crypto</span></div>
    <div class="ptag">SOLUSD<span>15M · Crypto</span></div>
    <div class="ptag">MNTUSD<span>15M · Crypto</span></div>
    <div class="ptag">MNTUSD<span>1H · Crypto</span></div>
    <div class="ptag">ZECUSD<span>15M · Crypto</span></div>
  </div>
</div>
<div class="footer">Cmvng Bot — Automated Trading Signal System &nbsp;·&nbsp; Built with precision</div>
<script>
fetch('/api/stats').then(r=>r.json()).then(d=>{
  document.getElementById('ls-total').textContent=d.total||'0';
  document.getElementById('ls-wr').textContent=(d.wr||0)+'%';
  document.getElementById('ls-pf').textContent=d.pf||'0';
}).catch(()=>{});
</script>
</body></html>"""

# ═══════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════

DASHBOARD_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cmvng Bot — Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#f4f9f4;--card:#fff;--green-1:#0d4a1a;--green-2:#1a6b2c;--green-3:#2e8b42;--green-4:#4caf50;--green-5:#81c784;--green-6:#c8e6c9;--green-7:#e8f5e9;--text:#0d2b0d;--text2:#3a6b3a;--text3:#6b9a6b;--border:#d4e8d4;--red:#c62828;--red-bg:#ffebee;--blue:#1565c0;--blue-bg:#e3f2fd;--amber:#e65100;--amber-bg:#fff8e1;--purple:#6a1b9a;--purple-bg:#f3e5f5}
body{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--text)}
.nav{padding:14px 32px;background:var(--green-1);display:flex;justify-content:space-between;align-items:center}
.nav h1{font-size:16px;font-weight:700;color:#fff;letter-spacing:-0.3px}
.nav h1 span{color:var(--green-5)}
.nav-r{display:flex;align-items:center;gap:14px}
.nav-badge{background:rgba(255,255,255,.15);color:var(--green-5);font-size:11px;padding:4px 12px;border-radius:20px;font-weight:600}
.nav-live{font-size:12px;color:var(--green-5);display:flex;align-items:center;gap:5px}
.nav-dot{width:6px;height:6px;background:var(--green-5);border-radius:50%;animation:p 2s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
.nav-date{font-size:12px;color:var(--green-5)}
.nav-home{color:var(--green-5);text-decoration:none;font-size:12px;font-weight:500;padding:4px 12px;border:1px solid rgba(255,255,255,.2);border-radius:16px}
.nav-home:hover{background:rgba(255,255,255,.1)}
.main{max-width:1200px;margin:0 auto;padding:24px 32px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;margin-bottom:28px}
.stat{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px 20px}
.slbl{font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;font-weight:700}
.sval{font-size:28px;font-weight:700;color:var(--green-1);font-family:'JetBrains Mono',monospace}
.sval.g{color:var(--green-3)}.sval.r{color:var(--red)}.sval.a{color:var(--amber)}
.section{margin-bottom:32px}
.stit{font-size:12px;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.1em;margin-bottom:14px;padding-bottom:8px;border-bottom:2px solid var(--green-6)}
.active-signals{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px;margin-bottom:28px}
.sig-card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:20px;position:relative;overflow:hidden}
.sig-card.buy{border-left:4px solid var(--green-3)}
.sig-card.sell{border-left:4px solid var(--red)}
.sig-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
.sig-pair{font-size:18px;font-weight:700;color:var(--green-1)}
.sig-dir{font-size:12px;font-weight:700;padding:3px 10px;border-radius:20px}
.sig-dir.buy{background:var(--green-7);color:var(--green-2)}
.sig-dir.sell{background:var(--red-bg);color:var(--red)}
.sig-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:14px}
.sg{text-align:center}
.sg-val{font-size:14px;font-weight:600;color:var(--green-1);font-family:'JetBrains Mono',monospace}
.sg-lbl{font-size:10px;color:var(--text3);text-transform:uppercase;margin-top:2px;font-weight:600}
.sig-meta{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.sig-tag{font-size:10px;font-weight:700;padding:2px 8px;border-radius:12px}
.sig-tag.t1{background:var(--green-7);color:var(--green-2)}
.sig-tag.t2{background:var(--amber-bg);color:var(--amber)}
.sig-tag.cry{background:var(--purple-bg);color:var(--purple)}
.sig-tag.filled{background:var(--green-7);color:var(--green-3)}
.sig-tag.waiting{background:var(--blue-bg);color:var(--blue)}
.sig-time{font-size:11px;color:var(--text3)}
.pgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-bottom:28px}
.pc{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px;border-left:4px solid var(--green-3)}
.pn{font-size:15px;font-weight:700;color:var(--green-1);margin-bottom:3px}
.pt{font-size:11px;color:var(--text3);margin-bottom:12px}
.pnums{display:flex;gap:16px}
.pnum{font-size:10px;color:var(--text3);font-weight:700;text-transform:uppercase}
.pnum span{display:block;font-size:16px;font-weight:700;color:var(--green-1);margin-top:2px;font-family:'JetBrains Mono',monospace}
.tbl-wrap{overflow-x:auto;background:var(--card);border:1px solid var(--border);border-radius:14px}
table{width:100%;border-collapse:collapse;font-size:13px;min-width:750px}
th{text-align:left;padding:12px 14px;font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid var(--border);background:var(--green-7);font-weight:700}
td{padding:12px 14px;border-bottom:1px solid #f0f9f0}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--green-7)}
.bdg{display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:700}
.pnd{background:var(--blue-bg);color:var(--blue)}
.tph{background:var(--green-7);color:var(--green-2)}
.slh{background:var(--red-bg);color:var(--red)}
.exp{background:#f5f5f5;color:#757575}
.buy-txt{color:var(--green-3);font-weight:700}
.sell-txt{color:var(--red);font-weight:700}
.mono{font-family:'JetBrains Mono',monospace;font-size:12px}
.empty{text-align:center;padding:48px;color:var(--text3);font-size:15px}
.footer{text-align:center;padding:20px;color:var(--text3);font-size:11px}
</style></head><body>
<nav class="nav">
  <h1>cmvng<span>bot</span> dashboard</h1>
  <div class="nav-r">
    <div class="nav-badge">Auto-monitoring ON</div>
    <div class="nav-date" id="hd"></div>
    <div class="nav-live"><div class="nav-dot"></div> Live</div>
    <a href="/" class="nav-home">Home</a>
  </div>
</nav>
<div class="main">
  <div class="stats">
    <div class="stat"><div class="slbl">Total Signals</div><div class="sval">{{ stats.total }}</div></div>
    <div class="stat"><div class="slbl">Win Rate</div><div class="sval {{ 'g' if stats.wr >= 45 else 'a' if stats.wr >= 35 else 'r' }}">{{ stats.wr }}%</div></div>
    <div class="stat"><div class="slbl">Profit Factor</div><div class="sval {{ 'g' if stats.pf >= 1.4 else 'a' if stats.pf >= 1.0 else 'r' }}">{{ stats.pf }}</div></div>
    <div class="stat"><div class="slbl">TP Hit</div><div class="sval g">{{ stats.tp }}</div></div>
    <div class="stat"><div class="slbl">SL Hit</div><div class="sval r">{{ stats.sl }}</div></div>
    <div class="stat"><div class="slbl">Pending</div><div class="sval a">{{ stats.pending }}</div></div>
  </div>

  {% if active_signals %}
  <div class="section">
    <div class="stit">Active Signals</div>
    <div class="active-signals">
      {% for s in active_signals %}
      <div class="sig-card {{ 'buy' if s.direction == 'BUY' else 'sell' }}">
        <div class="sig-top">
          <div class="sig-pair">{{ s.pair }}</div>
          <span class="sig-dir {{ 'buy' if s.direction == 'BUY' else 'sell' }}">{{ s.direction }}</span>
        </div>
        <div class="sig-grid">
          <div class="sg"><div class="sg-val">{{ s.entry }}</div><div class="sg-lbl">Entry</div></div>
          <div class="sg"><div class="sg-val" style="color:var(--red)">{{ s.sl }}</div><div class="sg-lbl">Stop Loss</div></div>
          <div class="sg"><div class="sg-val" style="color:var(--green-3)">{{ s.tp }}</div><div class="sg-lbl">Take Profit</div></div>
        </div>
        <div class="sig-meta">
          <span class="sig-tag {{ 't1' if s.category == 'Tier 1' else 't2' if s.category == 'Tier 2' else 'cry' }}">{{ s.category }}</span>
          <span class="sig-tag {{ 'filled' if s.filled else 'waiting' }}">{{ "Filled" if s.filled else "Waiting for entry" }}</span>
          <span class="sig-time">{{ s.timeframe }} · RR 1:{{ s.rr }} · {{ s.risk }}% risk</span>
        </div>
        <div style="margin-top:8px;font-size:11px;color:var(--text3)">
          Signal: {{ s.fired_at[:16].replace("T"," ") if s.fired_at else "" }}
          {% if s.filled_at %} · Filled: {{ s.filled_at[:16].replace("T"," ") }}{% endif %}
        </div>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endif %}

  {% if pair_stats %}
  <div class="section">
    <div class="stit">Pair Performance</div>
    <div class="pgrid">
      {% for p in pair_stats %}
      <div class="pc">
        <div class="pn">{{ p.pair }}</div>
        <div class="pt">{{ p.timeframe }} · <span style="font-weight:700">{{ p.category }}</span></div>
        <div class="pnums">
          <div class="pnum">Signals<span>{{ p.total }}</span></div>
          <div class="pnum">Wins<span style="color:var(--green-3)">{{ p.tp }}</span></div>
          <div class="pnum">Losses<span style="color:var(--red)">{{ p.sl }}</span></div>
        </div>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endif %}

  <div class="section">
    <div class="stit">Signal History</div>
    <div class="tbl-wrap">
      <table>
        <thead><tr><th>#</th><th>Pair</th><th>TF</th><th>Dir</th><th>Entry</th><th>SL</th><th>TP</th><th>RR</th><th>Risk</th><th>Cat</th><th>Filled</th><th>Status</th><th>Signal Time</th><th>Filled At</th><th>Closed At</th></tr></thead>
        <tbody>
          {% if not signals %}<tr><td colspan="15"><div class="empty">📡 No signals yet — waiting for the first alert</div></td></tr>{% endif %}
          {% for s in signals %}
          <tr>
            <td class="mono" style="color:var(--text3)">#{{ s.id }}</td>
            <td style="font-weight:700">{{ s.pair }}</td>
            <td style="color:var(--text3)">{{ s.timeframe }}</td>
            <td class="{{ 'buy-txt' if s.direction == 'BUY' else 'sell-txt' }}">{{ s.direction }}</td>
            <td class="mono">{{ s.entry }}</td>
            <td class="mono" style="color:var(--red)">{{ s.sl }}</td>
            <td class="mono" style="color:var(--green-3)">{{ s.tp }}</td>
            <td class="mono">1:{{ s.rr }}</td>
            <td>{{ s.risk }}%</td>
            <td><span class="sig-tag {{ 't1' if s.category == 'Tier 1' else 't2' if s.category == 'Tier 2' else 'cry' }}">{{ s.category }}</span></td>
            <td>{{ "✅" if s.filled else "⏳" }}</td>
            <td><span class="bdg {{ 'pnd' if s.status == 'Pending' else 'tph' if s.status == 'TP Hit' else 'slh' if s.status == 'SL Hit' else 'exp' }}">{{ s.status }}</span></td>
            <td style="color:var(--text3);font-size:12px" class="mono">{{ s.fired_at[:16].replace("T"," ") if s.fired_at else "" }}</td>
            <td style="color:var(--green-3);font-size:12px" class="mono">{{ s.filled_at[:16].replace("T"," ") if s.filled_at else "—" }}</td>
            <td style="color:var(--text3);font-size:12px" class="mono">{{ s.closed_at[:16].replace("T"," ") if s.closed_at else "—" }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
<div class="footer">Auto-refreshes every 60s · Prices monitored every 15 mins · Data stored in PostgreSQL</div>
<script>
setTimeout(()=>location.reload(),60000);
document.getElementById('hd').textContent=new Date().toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'});
</script>
</body></html>"""

# ═══════════════════════════════════════
# API STATS (for landing page)
# ═══════════════════════════════════════

@app.route("/api/stats")
def api_stats():
    try:
        conn = get_db()
        rows = conn.run("SELECT status FROM signals")
        conn.close()
        total = len(rows)
        tp = sum(1 for r in rows if r[0] == "TP Hit")
        sl = sum(1 for r in rows if r[0] == "SL Hit")
        closed = tp + sl
        wr = round(tp / closed * 100, 1) if closed > 0 else 0
        pf = round((tp * 1.5) / sl, 2) if sl > 0 else 0
        return jsonify({"total": total, "tp": tp, "sl": sl, "wr": wr, "pf": pf})
    except:
        return jsonify({"total": 0, "tp": 0, "sl": 0, "wr": 0, "pf": 0})

# ═══════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════

@app.route("/")
def landing():
    return render_template_string(LANDING_HTML)

@app.route("/dashboard")
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
    active_signals = [s for s in signals if s["status"] == "Pending"]
    return render_template_string(DASHBOARD_HTML, signals=signals, stats=stats,
                                  pair_stats=pair_stats, active_signals=active_signals)

@app.route("/test")
def test():
    send_telegram("✅ <b>Cmvng Bot is live!</b>\n\nPostgreSQL connected.\nAuto price monitoring active.\nLanding page + Dashboard ready.")
    return jsonify({"status": "ok"}), 200

# ═══════════════════════════════════════
# STARTUP — fix false SL hits + init
# ═══════════════════════════════════════

try:
    init_db()
    print("Database initialized OK")
    print("Database ready")
except Exception as e:
    print("DB init error: {}".format(e))

monitor_thread = threading.Thread(target=check_pending_signals, daemon=True)
monitor_thread.start()
print("Cmvng Bot started — monitor running")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
