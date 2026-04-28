# Broker MCP Tools Reference

> Generated: 2026-04-28  
> Covers: Robinhood (trayd), Tradier, TradeStation, Schwab, Webull

---

## 1. Robinhood — `trayd` MCP
> `https://mcp.trayd.ai/mcp` · Status: **Connected**

| Tool | Description | Suggested Prompt |
|------|-------------|-----------------|
| `link_robinhood` | Authenticate and link your Robinhood account via phone approval | "Link my Robinhood account" |
| `complete_robinhood_link` | Finalize linking after approving the phone notification | "I approved the notification, complete the link" |
| `submit_sms_code` | Submit SMS 2FA code if phone approval is unavailable | "Submit my SMS code: 123456" |
| `check_login_status` | Check whether your Robinhood session is active | "Am I still logged in to Robinhood?" |
| `list_accounts` | List all linked Robinhood accounts and their numbers | "List my Robinhood accounts" |
| `get_portfolio` | Portfolio summary: total equity, cash, buying power | "What is my Robinhood portfolio worth?" |
| `get_positions` | All current open positions with P&L | "Show me all my Robinhood positions" |
| `get_quote` | Real-time quote for a symbol | "Get a quote for AAPL on Robinhood" |
| `get_price` | Current price for a symbol | "What is the current price of TSLA?" |
| `get_open_orders` | List all pending / open orders | "Show my open orders on Robinhood" |
| `place_order` | Place a buy or sell order (market or limit) | "Buy 10 shares of NVDA at market on Robinhood" |
| `batch_place_order` | Submit multiple orders in a single call | "Place orders: buy 5 AAPL and sell 3 META" |
| `get_batch_status` | Check the status of a batch order submission | "What is the status of my batch order?" |
| `cancel_order` | Cancel an open order by ID | "Cancel my open order for TSLA" |
| `generate_api_key` | Generate a trayd API key for programmatic access | "Generate a trayd API key for me" |
| `logout` | Log out and unlink your Robinhood session | "Log me out of Robinhood" |

**Limitations:** No options chain, no Greeks, no options order types, no advanced order types (OCO/OTO).

---

## 2. Tradier MCP
> `https://mcp.tradier.com/mcp` · Status: **Connected**

| Tool | Description | Suggested Prompt |
|------|-------------|-----------------|
| `get_user_profile` | Retrieve account holder name and profile details | "Show my Tradier profile" |
| `get_account_balances` | Current balances: cash, equity, margin, buying power | "What are my Tradier account balances?" |
| `get_account_historical_balances` | Balance history over a date range | "Show my Tradier balance history for the past month" |
| `get_account_history` | Transaction history (trades, dividends, fees) | "Show my Tradier account history" |
| `get_positions` | All open positions with cost basis and market value | "Show my Tradier positions" |
| `get_orders` | List open and recent orders | "Show my Tradier orders" |
| `get_gainloss` | Realized gain/loss report | "Show my realized gains on Tradier" |
| `get_market_quotes` | Real-time or delayed quotes for one or more symbols | "Get quotes for AAPL, MSFT, GOOGL on Tradier" |
| `get_historical_data` | OHLCV price history for a symbol | "Get 6 months of daily price history for SPY" |
| `get_options_chain` | Full options chain with strikes, expiries, bid/ask, Greeks | "Show me the options chain for AAPL expiring next Friday" |
| `get_company_profile` | Fundamental company info (sector, description, employees) | "Give me the company profile for NVDA" |
| `get_market_calendar` | Market open/close schedule and holidays | "Is the market open this Friday?" |
| `get_watchlists` | Retrieve all saved watchlists | "Show my Tradier watchlists" |
| `add_to_watchlist` | Add a symbol to a watchlist | "Add PLTR to my Tradier watchlist" |
| `place_equity_order` | Place a stock order (market, limit, stop, stop-limit) | "Buy 100 shares of SPY at limit $520 on Tradier" |
| `place_option_order` | Place a single-leg option order | "Buy 1 AAPL $200 call expiring May 16 on Tradier" |
| `place_multileg_option_order` | Place multi-leg option strategies (spreads, condors, etc.) | "Place a bull call spread on SPY: buy 520C sell 525C May 16" |
| `place_oco_order` | One-Cancels-Other order (profit target + stop loss together) | "Place an OCO on TSLA: limit sell at $420, stop at $370" |
| `place_oto_order` | One-Triggers-Other order (entry triggers a follow-up order) | "Buy SPY at $520, then place a trailing stop 2%" |
| `place_otoco_order` | One-Triggers-OCO (entry + profit target + stop loss) | "Buy NVDA at market, then set $250 target and $200 stop" |
| `cancel_order` | Cancel an open order | "Cancel my open TSLA order on Tradier" |

**Strengths:** Full options support with Greeks, multi-leg strategies, advanced order types (OCO, OTO, OTOCO).

---

## 3. TradeStation MCP
> `https://mcp.tradestation.com/v2/mcp` · Status: **Connected**

### Session Setup
| Tool | Description | Suggested Prompt |
|------|-------------|-----------------|
| `ask-tradestation` | Required first call — loads account context and session rules | "Start a TradeStation session" |
| `get-trading-environment` | Returns current environment (Live / Sim) — must be shown to user | "What trading environment am I in on TradeStation?" |
| `set-trading-environment` | Switch between Live and Simulation trading | "Switch TradeStation to simulation mode" |

### Accounts & Balances
| Tool | Description | Suggested Prompt |
|------|-------------|-----------------|
| `get-accounts` | List all accessible brokerage accounts with types and status | "Show my TradeStation accounts" |
| `get-balances-summary` | Aggregate balances by account type with top winners/losers | "Show my TradeStation balance summary" |
| `get-balances-details` | Detailed real-time + BOD balances per account; optional 90-day history | "Show detailed balances for my TradeStation margin account" |
| `get-portfolio-asset-breakdown` | Portfolio breakdown by asset class (stocks, options, futures) | "Break down my TradeStation portfolio by asset type" |

### Positions
| Tool | Description | Suggested Prompt |
|------|-------------|-----------------|
| `get-positions-summary` | High-level positions summary across all accounts | "Show a summary of my TradeStation positions" |
| `get-positions-details` | Detailed positions with filters (symbol, gaining/losing, position ID) | "Show my losing positions on TradeStation" |

### Orders
| Tool | Description | Suggested Prompt |
|------|-------------|-----------------|
| `get-orders-overview` | Overview of current open orders grouped by status/type | "Show an overview of my open TradeStation orders" |
| `get-orders-detailed` | Full detail on current open orders with optional filters | "Show details for my open AAPL orders on TradeStation" |
| `get-historical-orders-overview` | Summary of historical orders (up to 89 days) grouped by dimension | "Show an overview of my TradeStation order history" |
| `get-historical-orders-detailed` | Full historical order detail with filters (symbol, date, type, status) | "Show all filled TSLA orders on TradeStation this month" |
| `place-order` | Place equity, futures, or options orders | "Buy 10 shares of NVDA at market on TradeStation" |
| `confirm-order` | Preview/confirm an order before submitting | "Preview my TradeStation order before placing it" |
| `replace-order` | Modify an existing open order | "Change my AAPL limit order to $180 on TradeStation" |
| `cancel-order` | Cancel an open order | "Cancel my open MSFT order on TradeStation" |

### Market Data
| Tool | Description | Suggested Prompt |
|------|-------------|-----------------|
| `get-quotes` | Real-time quotes for one or more symbols | "Get quotes for SPY and QQQ on TradeStation" |
| `get-bars` | OHLCV historical bars (any interval: minute, daily, weekly) | "Get 30-day daily bars for AAPL on TradeStation" |

### Options
| Tool | Description | Suggested Prompt |
|------|-------------|-----------------|
| `get-option-expirations` | List all available expiration dates for a symbol | "What option expirations are available for SPY?" |
| `get-option-strikes` | Available strikes for a symbol and expiration | "Show me AAPL option strikes for May 16" |
| `get-option-quotes` | Real-time quotes for specific option contracts | "Get quotes for AAPL $200 calls expiring May 16" |
| `get-option-chain-snapshot` | Full options chain snapshot with Greeks and IV | "Show me the full SPY options chain for next Friday" |
| `get-option-spread-types` | List supported spread types for strategy orders | "What option spread types does TradeStation support?" |
| `get-option-risk-reward` | Risk/reward analysis for an option strategy | "What's the risk/reward on my SPY bull call spread?" |

**Strengths:** Full options chain with Greeks, 89-day order history, futures support, Live/Sim environment switching, risk/reward analysis, order preview before placement.

---

## 4. Schwab — `schwab-smartspreads-file` MCP
> Local Dev · Status: **Connected**

| Tool | Description | Suggested Prompt |
|------|-------------|-----------------|
| `get_account_summary` | Account-level summary: equity, cash, margin | "Show my Schwab account summary" |
| `get_positions` | All open positions | "Show my Schwab positions" |
| `get_equity_positions` | Stock/ETF positions only | "Show only my stock positions on Schwab" |
| `get_futures_positions` | Active futures positions | "Show my Schwab futures positions" |
| `get_all_positions_summary` | Combined summary across all asset classes | "Give me a full positions summary on Schwab" |
| `get_live_quote` | Real-time quote for a symbol | "Get a live quote for /ES on Schwab" |
| `get_futures_quote` | Live quote for a futures contract | "What is the current price of /NQ?" |
| `get_quotes_batch` | Quotes for multiple symbols at once | "Get quotes for SPY, QQQ, IWM on Schwab" |
| `get_recent_bars` | Recent OHLCV bars for a symbol | "Show the last 10 daily bars for AAPL on Schwab" |
| `get_spread_value` | Value of a futures spread (cached) | "What is the value of the /ES Dec-Mar spread?" |
| `get_spread_value_live` | Live value of a futures spread | "Get the live value of the /CL Nov-Dec spread" |
| `get_butterfly_value` | Value of a futures butterfly spread | "What is the /ZW butterfly value?" |
| `get_watchlist_quotes` | Quotes for all symbols in your Schwab watchlist | "Show quotes for my Schwab watchlist" |
| `get_trade_history` | Historical trade records | "Show my Schwab trade history for April" |
| `get_transactions` | Account transaction log (fills, transfers, fees) | "Show my recent Schwab transactions" |
| `get_market_hours` | Market open/close times for today | "Is the futures market open right now?" |
| `get_seasonal_days_remaining` | Days remaining in a seasonal trade window | "How many days are left in the seasonal window?" |
| `get_stream_status` | Status of the live market data stream | "Is the Schwab data stream active?" |
| `check_target_distance` | Distance between current price and a spread target | "How far is /ES from my target price?" |
| `import_tos_pnl` | Import Think or Swim P&L data into the system | "Import my TOS P&L export file" |

**Strengths:** Futures spreads, butterfly values, seasonal analysis, live streaming data. Oriented toward spread trading strategies.

---

## 5. Webull MCP
> Local Dev · Status: **Connected**

### Account
| Tool | Description | Suggested Prompt |
|------|-------------|-----------------|
| `get_account_list` | List all Webull accounts | "Show my Webull accounts" |
| `get_account_balance` | Account balances and buying power | "What is my Webull account balance?" |
| `get_account_positions` | All positions across the account | "Show my Webull positions" |
| `get_open_orders` | Pending/open orders | "Show my open orders on Webull" |
| `get_order_detail` | Detail for a specific order by ID | "Show details for Webull order #12345" |
| `get_order_history` | Historical order log | "Show my Webull order history for this week" |

### Stocks
| Tool | Description | Suggested Prompt |
|------|-------------|-----------------|
| `get_instruments` | Search for stock instruments by symbol | "Look up AAPL instrument on Webull" |
| `get_stock_quotes` | Real-time stock quotes | "Get a quote for MSFT on Webull" |
| `get_stock_snapshot` | Full market snapshot for a stock | "Show me the full snapshot for GOOGL on Webull" |
| `get_stock_bars` | OHLCV bars for a stock | "Get daily bars for TSLA for the last 30 days" |
| `get_stock_bars_single` | Single bar for a stock at a specific time | "Get the 1-minute bar for SPY at 9:45am today" |
| `get_stock_tick` | Tick-by-tick trade data | "Show tick data for NVDA on Webull" |
| `get_stock_footprint` | Volume footprint / order flow data | "Show the footprint chart for AAPL on Webull" |
| `preview_stock_order` | Preview a stock order before placing | "Preview buying 50 shares of AMZN at market on Webull" |
| `place_stock_order` | Place a stock buy or sell order | "Buy 10 shares of NVDA at market on Webull" |
| `place_stock_combo_order` | Place a stock combo (bracket) order | "Buy SPY with a bracket: target $525, stop $515" |
| `place_algo_order` | Place an algorithmic order (TWAP, VWAP, etc.) | "Place a VWAP order to buy 500 shares of QQQ" |
| `replace_stock_order` | Modify an existing stock order | "Change my AAPL limit order to $175" |

### Options
| Tool | Description | Suggested Prompt |
|------|-------------|-----------------|
| `preview_option_order` | Preview an option order before placing | "Preview buying 1 SPY $520 call for May 16" |
| `place_option_single_order` | Place a single-leg option order | "Buy 2 TSLA $400 puts expiring May 16 on Webull" |
| `place_option_strategy_order` | Place a multi-leg option strategy | "Place a put spread on SPY: buy $515P sell $510P May 16" |
| `replace_option_order` | Modify an existing option order | "Change my SPY call order to a limit of $3.50" |

### Futures
| Tool | Description | Suggested Prompt |
|------|-------------|-----------------|
| `get_futures_products` | List available futures products | "What futures products are available on Webull?" |
| `get_futures_instruments` | Futures contracts for a product | "Show /ES contracts available on Webull" |
| `get_futures_instruments_by_code` | Lookup a futures instrument by code | "Look up the /NQ Dec contract on Webull" |
| `get_futures_snapshot` | Full market snapshot for a futures contract | "Show the /ES snapshot on Webull" |
| `get_futures_bars` | OHLCV bars for a futures contract | "Get hourly bars for /CL this week on Webull" |
| `get_futures_tick` | Tick data for a futures contract | "Show tick data for /NQ on Webull" |
| `get_futures_depth` | Level 2 order book for futures | "Show the order book depth for /ES" |
| `get_futures_footprint` | Volume footprint / order flow for futures | "Show the footprint chart for /NQ" |
| `place_futures_order` | Place a futures order | "Buy 1 /ES contract at market on Webull" |
| `replace_futures_order` | Modify a futures order | "Change my /NQ limit order to 18500" |

### Crypto
| Tool | Description | Suggested Prompt |
|------|-------------|-----------------|
| `get_crypto_instruments` | List available crypto instruments | "What crypto can I trade on Webull?" |
| `get_crypto_snapshot` | Real-time crypto market snapshot | "Show the BTC snapshot on Webull" |
| `get_crypto_bars` | OHLCV bars for a crypto asset | "Get daily bars for ETH this month on Webull" |
| `place_crypto_order` | Place a crypto buy or sell order | "Buy $500 worth of BTC on Webull" |

### Events / Economic Data
| Tool | Description | Suggested Prompt |
|------|-------------|-----------------|
| `get_event_categories` | List event categories (earnings, economic, etc.) | "What event categories are available on Webull?" |
| `get_event_instruments` | Instruments tied to an event | "What stocks report earnings this week on Webull?" |
| `get_event_events` | List events for a category/date range | "Show upcoming Fed events on Webull" |
| `get_event_series` | Time series data for an economic event | "Show the CPI series history on Webull" |
| `get_event_snapshot` | Snapshot of a specific event | "Show the latest jobs report snapshot on Webull" |
| `get_event_bars` | Price bars around an event | "Show SPY bars around the last FOMC meeting" |
| `get_event_depth` | Order book around an event | "Show /ES depth around CPI release" |
| `get_event_tick` | Tick data around an event | "Show tick data for NVDA around its earnings" |
| `place_event_order` | Place an order tied to an event trigger | "Place an event order for TSLA earnings" |
| `replace_event_order` | Modify an event-triggered order | "Update my TSLA earnings order limit price" |
| `cancel_order` | Cancel any open order | "Cancel my open Webull order" |

**Strengths:** Most asset classes in one MCP (stocks, options, futures, crypto, events/economic data), order flow / footprint data, algo orders, Level 2 depth.

---

## Quick Comparison

| Capability | Robinhood | Tradier | TradeStation | Schwab | Webull |
|-----------|:---------:|:-------:|:------------:|:------:|:------:|
| Stock trading | ✅ | ✅ | ✅ | ✅ | ✅ |
| Options (single leg) | ❌ | ✅ | ✅ | ❌ | ✅ |
| Options (multi-leg) | ❌ | ✅ | ✅ | ❌ | ✅ |
| Options Greeks | ❌ | ✅ | ✅ | ❌ | ❌ |
| Options risk/reward analysis | ❌ | ❌ | ✅ | ❌ | ❌ |
| Advanced orders (OCO/OTO) | ❌ | ✅ | ✅ | ❌ | ✅ |
| Order preview before placing | ❌ | ❌ | ✅ | ❌ | ✅ |
| Live / Sim environment toggle | ❌ | ❌ | ✅ | ❌ | ❌ |
| Order history (days back) | ❌ | ~90d | 89d | ✅ | ✅ |
| Futures trading | ❌ | ❌ | ✅ | ✅ | ✅ |
| Futures spreads | ❌ | ❌ | ❌ | ✅ | ❌ |
| Crypto trading | ❌ | ❌ | ❌ | ❌ | ✅ |
| Algo orders | ❌ | ❌ | ❌ | ❌ | ✅ |
| Level 2 / Footprint | ❌ | ❌ | ❌ | ❌ | ✅ |
| Economic event data | ❌ | ❌ | ❌ | ❌ | ✅ |
| Batch orders | ✅ | ❌ | ❌ | ❌ | ❌ |
| Historical balance (90d) | ❌ | ❌ | ✅ | ❌ | ❌ |
