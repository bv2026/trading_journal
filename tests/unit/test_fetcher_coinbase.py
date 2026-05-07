from src.fetchers.coinbase import normalize_futures, normalize_positions, normalize_instruments


def test_normalize_coinbase_accounts_payload():
    resp = {
        "accounts": [
            {
                "currency": {"code": "BTC", "name": "Bitcoin"},
                "available_balance": {"value": "0.5"},
                "native_balance": {"amount": "35000"},
            },
            {
                "currency": {"code": "USD", "name": "US Dollar"},
                "available_balance": {"value": "100"},
                "native_balance": {"amount": "100"},
            },
        ]
    }

    rows = normalize_positions(resp)

    by_symbol = {row["symbol"]: row for row in rows}
    assert len(rows) == 2
    assert by_symbol["BTC"]["account_id"] == "COINBASE"
    assert by_symbol["BTC"]["qty"] == 0.5
    assert by_symbol["BTC"]["market_value"] == 35000.0
    assert by_symbol["BTC"]["price"] == 70000.0
    assert by_symbol["BTC"]["source_file"] is None
    assert by_symbol["USD"]["market_value"] == 100.0


def test_normalize_coinbase_positions_payload():
    rows = normalize_positions({
        "positions": [
            {"symbol": "ETH", "quantity": "2", "price": "4000"},
        ]
    })

    assert rows[0]["symbol"] == "ETH"
    assert rows[0]["market_value"] == 8000.0


def test_normalize_coinbase_snapshot_balances_payload():
    rows = normalize_positions({
        "balances": [
            {
                "asset": "ATOM",
                "total": "75.324",
                "price_usd": "1.923",
                "usd_value": "144.848052",
                "metadata": {"name": "ATOM Wallet"},
            },
            {
                "asset": "USD",
                "total": "12.34",
                "price_usd": "1",
                "usd_value": "12.34",
            },
        ]
    })

    by_symbol = {row["symbol"]: row for row in rows}
    assert len(rows) == 2
    assert by_symbol["ATOM"]["name"] == "ATOM Wallet"
    assert by_symbol["ATOM"]["qty"] == 75.324
    assert by_symbol["ATOM"]["price"] == 1.923
    assert by_symbol["ATOM"]["market_value"] == 144.848052
    assert by_symbol["USD"]["market_value"] == 12.34


def test_normalize_coinbase_portfolio_state_includes_futures_usd():
    rows = normalize_positions({
        "balances": [
            {"asset": "USDC", "total": "10", "usd_value": "10", "price_usd": "1"},
        ],
        "futures_balance_summary": {
            "total_usd_balance": "7290.43",
        },
    })

    by_symbol = {row["symbol"]: row for row in rows}
    assert by_symbol["USDC"]["market_value"] == 10.0
    assert by_symbol["USD"]["name"] == "Coinbase Derivatives USD"
    assert by_symbol["USD"]["qty"] == 7290.43
    assert by_symbol["USD"]["market_value"] == 7290.43


def test_normalize_coinbase_futures_adds_realized_funding_adjustment():
    rows = normalize_futures({
        "positions": [
            {
                "product_id": "BTC-20DEC30-CDE",
                "side": "LONG",
                "contracts": "2",
                "mark_price": "81500",
                "unrealized_pnl": "200",
            },
            {
                "product_id": "ETH-20DEC30-CDE",
                "side": "SHORT",
                "contracts": "3",
                "mark_price": "2350",
                "unrealized_pnl": "-50",
            },
        ],
        "futures_balance_summary": {
            "unrealized_pnl": "160",
            "daily_realized_pnl": "1000",
            "funding_pnl": "1.5",
        },
    })

    by_symbol = {row["symbol"]: row for row in rows}
    assert by_symbol["BTC-20DEC30-CDE"]["qty"] == 2.0
    assert by_symbol["BTC-20DEC30-CDE"]["market_value"] == 200.0
    assert by_symbol["ETH-20DEC30-CDE"]["qty"] == -3.0
    assert by_symbol["ETH-20DEC30-CDE"]["market_value"] == -50.0
    assert by_symbol["COINBASE-FUTURES-PNL-ADJ"]["market_value"] == 1011.5


def test_normalize_coinbase_futures_derives_cost_basis_from_entry_price():
    rows = normalize_futures({
        "positions": [
            {
                "product_id": "BTC-20DEC30-CDE",
                "side": "LONG",
                "contracts": "2",
                "avg_entry_price": "79000",
                "mark_price": "81500",
                "unrealized_pnl": "200",
            },
        ],
    })

    assert rows[0]["cost_basis"] == 158000.0


def test_normalize_instruments_deduplicates():
    instruments = normalize_instruments([
        {"symbol": "BTC", "name": "Bitcoin"},
        {"symbol": "BTC", "name": "Bitcoin"},
    ])

    assert len(instruments) == 1
    assert instruments[0]["asset_class"] == "crypto"
    assert instruments[0]["exchange"] == "Coinbase"
