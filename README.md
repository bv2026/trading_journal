# Trading Journal

A personal portfolio tracker that consolidates activity across multiple brokerage accounts into a single SQLite database and Streamlit dashboard.

## Accounts supported

| Account ID | Broker       | Type       |
|------------|--------------|------------|
| RH-BV      | Robinhood    | Investment |
| RH-KD      | Robinhood    | Investment |
| WEBULL     | Webull       | Investment + Cash |
| TS         | TradeStation | Investment |
| SCHWAB     | Schwab       | Investment |
| TRADIER    | Tradier      | Investment |
| COINBASE   | Coinbase     | Crypto     |
| FIDELITY   | Fidelity     | Investment |

## What it tracks

- **Cash Flow** — deposits and withdrawals
- **Dividends** — cash, reinvested, manufactured, non-qualified
- **Rewards** — staking, interest, securities lending, platform rewards
- **Margin Interest** — monthly charges across all margin accounts
- **Fees** — trading fees, subscription fees, clearing fees
- **Crypto Flow** — Coinbase USD/USDC deposits, withdrawals, sends and receives

Positions and unrealised P&L are out of scope.

## Project structure

```
trading-journal/
├── activity/               Broker CSV exports (gitignored)
├── data/                   SQLite database (gitignored)
│   └── journal.db
├── src/
│   ├── db.py               Database helpers (init, upsert, load)
│   └── parsers/
│       ├── utils.py        Shared utilities (parse_amount, parse_date, make_id)
│       ├── robinhood.py
│       ├── webull.py
│       ├── tradestation.py
│       ├── schwab.py
│       ├── tradier.py
│       ├── coinbase.py
│       └── fidelity.py     Yearly summary parser (2020+)
├── dashboard/
│   └── app.py              Streamlit dashboard
├── ingest.py               Load all CSVs → journal.db
├── schema.sql              Table definitions
├── requirements.txt
└── README.md
```

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Usage

See [USAGE.md](USAGE.md) for full details.

**Quick start:**

```bash
# 1. Drop broker CSV exports into activity/
# 2. Ingest
python ingest.py

# 3. Launch dashboard
streamlit run dashboard/app.py
```

Dashboard runs at `http://localhost:8501`.
