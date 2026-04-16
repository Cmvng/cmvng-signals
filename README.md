# Cmvng Bot — Signal System

## Setup Instructions

### 1. Deploy to Railway
1. Push this folder to a GitHub repository
2. Go to railway.app → New Project → Deploy from GitHub
3. Select your repository
4. Add these environment variables in Railway settings:
   - `TELEGRAM_TOKEN` = your bot token
   - `TELEGRAM_CHAT_ID` = your chat ID

### 2. Set up TradingView Alerts
For each pair, create an alert with these settings:
- Condition: Your strategy signal
- Webhook URL: https://YOUR-RAILWAY-URL/webhook
- Message: (copy from the pair templates below)

### 3. Alert Message Templates

#### XAUUSD 30M BUY:
```json
{"pair":"XAUUSD","timeframe":"30M","direction":"BUY","entry":"{{plot_0}}","sl":"{{plot_1}}","tp":"{{plot_2}}"}
```

#### XAUUSD 30M SELL:
```json
{"pair":"XAUUSD","timeframe":"30M","direction":"SELL","entry":"{{plot_0}}","sl":"{{plot_1}}","tp":"{{plot_2}}"}
```

Replace XAUUSD/30M with your pair and timeframe for each alert.

### 4. TradingView Plot Variables
In your Pine Script strategy, make sure these are plotted:
- plot_0 = entry price
- plot_1 = SL price  
- plot_2 = TP price

Add these lines to your strategy if not already there:
```pine
plot(b_entry, title="Entry", display=display.none)
plot(b_sl,    title="SL",    display=display.none)
plot(b_tp1,   title="TP",    display=display.none)
```

### 5. Test the system
Visit: https://YOUR-RAILWAY-URL/test
This sends a test message to your Telegram.

### 6. Dashboard
Visit: https://YOUR-RAILWAY-URL/
This shows all signals, stats, and per-pair performance.

## Updating Signal Outcomes
On the dashboard, click TP / SL / Exp buttons to mark each signal result.
This updates your live win rate and profit factor automatically.
