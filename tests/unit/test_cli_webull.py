from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.cli.webull import orders_to_import_rows, parse_order_history_text


WEBULL_ORDER_HISTORY = """
=== Order History ===

[Order Entry 1]
Client Order ID:  ABC

  [Order Details]
    Client Order ID:    ABC
    Order ID:           ORD1
    Symbol:             ACHR
    Side:               BUY
    Status:             FILLED
    Order Type:         MARKET
    Instrument Type:    EQUITY
    Total Quantity:     67.58508
    Filled Quantity:    67.58508
    Filled Price:       6.17
    Time In Force:      DAY
    Place Time:         2026-05-06T13:56:01.135Z
    Filled Time:        N/A

[Order Entry 2]
Client Order ID:  DEF

  [Order Details]
    Client Order ID:    DEF
    Order ID:           ORD2
    Symbol:             SU
    Side:               SELL
    Status:             CANCELLED
    Order Type:         LIMIT
    Instrument Type:    EQUITY
    Total Quantity:     60
    Filled Quantity:    0
    Filled Price:       N/A
    Time In Force:      DAY
    Place Time:         2026-05-06T13:32:41.000Z
"""


def test_parse_order_history_text_extracts_order_details():
    orders = parse_order_history_text(WEBULL_ORDER_HISTORY)

    assert len(orders) == 2
    assert orders[0]["Symbol"] == "ACHR"
    assert orders[0]["Side"] == "BUY"
    assert orders[0]["Filled Quantity"] == "67.58508"


def test_orders_to_import_rows_keeps_only_filled_orders():
    rows = orders_to_import_rows(parse_order_history_text(WEBULL_ORDER_HISTORY))

    assert rows == [{
        "Date": "5/6/2026",
        "Time": "9:56:01",
        "O/C": "Buy",
        "L/S": "",
        "Ticker": "ACHR",
        "Sh/Contr": "67.58508",
        "Price": "6.17",
        "Comm": "",
        "Amount": "",
        "Type/Mult": "",
    }]
