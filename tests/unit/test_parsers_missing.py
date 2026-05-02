# -*- coding: utf-8 -*-
"""Unit tests for parsers that had no coverage:
   schwab, tradier, tradestation, webull, fidelity.
"""
import io
import csv
import textwrap
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.parsers.schwab      import parse as schwab_parse
from src.parsers.tradier     import parse as tradier_parse, _categorize as tradier_cat
from src.parsers.tradestation import parse as ts_parse, _categorize as ts_cat
from src.parsers.webull      import parse_inv, parse_cash
from src.parsers.fidelity    import parse as fidelity_parse, _year_date


# ── CSV helpers ───────────────────────────────────────────────────────────────

def _write_csv(tmp_path: Path, filename: str, rows: list[dict]) -> Path:
    p = tmp_path / filename
    if not rows:
        p.write_text("")
        return p
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return p


# ═══ SCHWAB PARSER ═══════════════════════════════════════════════════════════

SCHWAB_COLS = ["Date", "Action", "Symbol", "Description", "Quantity",
               "Price", "Fees & Comm", "Amount"]


class TestSchwabParser:
    def _csv(self, tmp_path, rows):
        return _write_csv(tmp_path, "schwab.csv", rows)

    def _row(self, action, amount, date="04/01/2026", symbol="AAPL",
             desc="Apple Inc", fees=0):
        return {"Date": date, "Action": action, "Symbol": symbol,
                "Description": desc, "Quantity": "", "Price": "",
                "Fees & Comm": fees, "Amount": amount}

    def test_dividend_row(self, tmp_path):
        p = self._csv(tmp_path, [self._row("Cash Dividend", "$50.00")])
        records = schwab_parse(str(p), "SCHWAB")
        assert len(records) == 1
        r = records[0]
        assert r["category"] == "dividend"
        assert r["subcategory"] == "cash_div"
        assert r["amount"] == pytest.approx(50.0)

    def test_moneylink_deposit(self, tmp_path):
        p = self._csv(tmp_path, [self._row("MoneyLink Transfer", "$5000.00", symbol="")])
        records = schwab_parse(str(p), "SCHWAB")
        assert len(records) == 1
        assert records[0]["category"] == "cash_flow"
        assert records[0]["subcategory"] == "deposit"

    def test_moneylink_withdrawal(self, tmp_path):
        p = self._csv(tmp_path, [self._row("MoneyLink Transfer", "-$2000.00", symbol="")])
        records = schwab_parse(str(p), "SCHWAB")
        assert records[0]["subcategory"] == "withdrawal"

    def test_margin_interest(self, tmp_path):
        p = self._csv(tmp_path, [self._row("Margin Interest", "-$45.00")])
        records = schwab_parse(str(p), "SCHWAB")
        assert records[0]["category"] == "margin_interest"

    def test_skip_buy_action(self, tmp_path):
        p = self._csv(tmp_path, [self._row("Buy", "$0.00")])
        records = schwab_parse(str(p), "SCHWAB")
        assert records == []

    def test_skip_sell_action(self, tmp_path):
        p = self._csv(tmp_path, [self._row("Sell", "$0.00")])
        assert schwab_parse(str(p), "SCHWAB") == []

    def test_fee_row_appended_for_commission(self, tmp_path):
        p = self._csv(tmp_path, [self._row("Cash Dividend", "$50.00", fees="$1.50")])
        records = schwab_parse(str(p), "SCHWAB")
        assert len(records) == 2
        fee_rec = next(r for r in records if r["category"] == "fee")
        assert fee_rec["subcategory"] == "commission"
        assert fee_rec["amount"] < 0

    def test_unknown_action_skipped(self, tmp_path):
        p = self._csv(tmp_path, [self._row("Unknown Action XYZ", "$10.00")])
        assert schwab_parse(str(p), "SCHWAB") == []

    def test_missing_date_skipped(self, tmp_path):
        p = self._csv(tmp_path, [self._row("Cash Dividend", "$10.00", date="")])
        assert schwab_parse(str(p), "SCHWAB") == []

    def test_ids_are_stable(self, tmp_path):
        row = self._row("Cash Dividend", "$50.00")
        p = self._csv(tmp_path, [row])
        r1 = schwab_parse(str(p), "SCHWAB")
        r2 = schwab_parse(str(p), "SCHWAB")
        assert r1[0]["id"] == r2[0]["id"]

    def test_account_id_propagated(self, tmp_path):
        p = self._csv(tmp_path, [self._row("Cash Dividend", "$50.00")])
        records = schwab_parse(str(p), "MY_SCHWAB")
        assert records[0]["account_id"] == "MY_SCHWAB"

    def test_non_qualified_div(self, tmp_path):
        p = self._csv(tmp_path, [self._row("Non-Qualified Div", "$30.00")])
        records = schwab_parse(str(p), "SCHWAB")
        assert records[0]["subcategory"] == "nonqualified_div"

    def test_reinvest_dividend(self, tmp_path):
        p = self._csv(tmp_path, [self._row("Reinvest Dividend", "$20.00")])
        records = schwab_parse(str(p), "SCHWAB")
        assert records[0]["subcategory"] == "reinvested_div"


# ═══ TRADIER PARSER ══════════════════════════════════════════════════════════

class TestTradierCategorize:
    def test_ach_deposit(self):
        assert tradier_cat("ACH DEPOSIT FROM BANK", 1000) == ("cash_flow", "deposit")

    def test_ach_withdrawal(self):
        assert tradier_cat("ACH WITHDRAWAL TO BANK", -500) == ("cash_flow", "withdrawal")

    def test_margin_interest(self):
        cat, sub = tradier_cat("FROM 04/01 THRU 04/30 MARGIN", -30)
        assert cat == "margin_interest"
        assert sub == "monthly"

    def test_dividend(self):
        assert tradier_cat("CASH DIV ON 100 SHS", 50) == ("dividend", "cash_div")

    def test_non_qualified_div(self):
        assert tradier_cat("NON-QUALIFIED DIVIDEND AAPL", 10) == ("dividend", "cash_div")

    def test_option_settlement(self):
        cat, sub = tradier_cat("CALL AAPL 06/20/26 SETTLEMENT", -100)
        assert cat == "other"

    def test_clearing_fee(self):
        assert tradier_cat("CLEARING FEE", -0.50) == ("fee", "clearing_fee")

    def test_star_dividend(self):
        cat, sub = tradier_cat("***AAPL DIV", 5)
        assert cat == "dividend"

    def test_fallthrough(self):
        cat, sub = tradier_cat("SOME UNKNOWN THING", 0)
        assert cat == "other"


class TestTradierParser:
    COLS = ["Type", "Description", "Net Amount", "Trade Date", "Symbol"]

    def _csv(self, tmp_path, rows):
        return _write_csv(tmp_path, "tradier.csv", rows)

    def _row(self, desc, amount, date="2026-04-01", symbol="AAPL",
             row_type="MONEY_MOVEMENTS"):
        return {"Type": row_type, "Description": desc,
                "Net Amount": amount, "Trade Date": date, "Symbol": symbol}

    def test_dividend_row(self, tmp_path):
        p = self._csv(tmp_path, [self._row("CASH DIV ON 50 SHS AAPL", "25.00")])
        records = tradier_parse(str(p), "TRADIER")
        assert len(records) == 1
        assert records[0]["category"] == "dividend"
        assert records[0]["amount"] == pytest.approx(25.0)

    def test_non_money_movement_skipped(self, tmp_path):
        p = self._csv(tmp_path, [self._row("BUY 10 AAPL", "1000", row_type="TRADE")])
        assert tradier_parse(str(p), "TRADIER") == []

    def test_margin_interest_is_negative(self, tmp_path):
        p = self._csv(tmp_path, [self._row("FROM 04/01 THRU 04/30 MARGIN", "30.00")])
        records = tradier_parse(str(p), "TRADIER")
        assert records[0]["amount"] < 0

    def test_missing_date_skipped(self, tmp_path):
        p = self._csv(tmp_path, [self._row("CASH DIV", "10", date="")])
        assert tradier_parse(str(p), "TRADIER") == []

    def test_ach_deposit(self, tmp_path):
        p = self._csv(tmp_path, [self._row("ACH DEPOSIT FROM BANK", "5000")])
        records = tradier_parse(str(p), "TRADIER")
        assert records[0]["category"] == "cash_flow"
        assert records[0]["subcategory"] == "deposit"

    def test_id_stability(self, tmp_path):
        row = self._row("CASH DIV ON 50 SHS AAPL", "25.00")
        p = self._csv(tmp_path, [row])
        r1 = tradier_parse(str(p), "TRADIER")
        r2 = tradier_parse(str(p), "TRADIER")
        assert r1[0]["id"] == r2[0]["id"]


# ═══ TRADESTATION PARSER ═════════════════════════════════════════════════════

class TestTsCategorize:
    def test_cash_received_ach(self):
        assert ts_cat("Cash Received ACH from bank", 5000) == ("cash_flow", "deposit")

    def test_ach_withdrawal(self):
        assert ts_cat("ACH Withdrawal to bank", -2000) == ("cash_flow", "withdrawal")

    def test_internal_transfer_to(self):
        cat, sub = ts_cat("JNL TO account 123", 0)
        assert cat == "cash_flow"
        assert sub == "internal_transfer"

    def test_internal_transfer_from(self):
        cat, sub = ts_cat("JNL FROM account 456", 0)
        assert cat == "cash_flow"
        assert sub == "internal_transfer"

    def test_tradestation_charges(self):
        assert ts_cat("TradeStation Charges platform fee", -10) == ("fee", "platform_fee")

    def test_fpl_interest(self):
        cat, sub = ts_cat("FPL INTEREST on securities", 5)
        assert cat == "reward"
        assert sub == "securities_lending"

    def test_margin_interest_regex(self):
        cat, sub = ts_cat("12.50000%05/01-05/30  $738", -738)
        assert cat == "margin_interest"
        assert sub == "monthly"

    def test_fallthrough_is_dividend(self):
        cat, sub = ts_cat("SOME FUND DISTRIBUTION", 100)
        assert cat == "dividend"
        assert sub == "cash_div"

    def test_returned_ach_fee(self):
        cat, sub = ts_cat("RETURNED ACH FEE", -35)
        assert cat == "fee"


class TestTsParser:
    def _csv(self, tmp_path, rows):
        return _write_csv(tmp_path, "ts.csv", rows)

    def test_basic_deposit(self, tmp_path):
        p = self._csv(tmp_path, [
            {"Date": "04/01/2026", "Description": "Cash Received ACH from bank",
             "Amount": "$5,000.00"}
        ])
        records = ts_parse(str(p), "TS")
        assert len(records) == 1
        assert records[0]["category"] == "cash_flow"
        assert records[0]["amount"] == pytest.approx(5000.0)

    def test_blank_description_skipped(self, tmp_path):
        p = self._csv(tmp_path, [
            {"Date": "04/01/2026", "Description": "", "Amount": "$0.00"}
        ])
        assert ts_parse(str(p), "TS") == []

    def test_margin_interest(self, tmp_path):
        p = self._csv(tmp_path, [
            {"Date": "05/31/2026", "Description": "12.50000%05/01-05/30  $738",
             "Amount": "-$738.00"}
        ])
        records = ts_parse(str(p), "TS")
        assert records[0]["category"] == "margin_interest"

    def test_id_stable_across_runs(self, tmp_path):
        row = {"Date": "04/01/2026", "Description": "Cash Received ACH from bank",
               "Amount": "$5,000.00"}
        p = self._csv(tmp_path, [row])
        r1 = ts_parse(str(p), "TS")
        r2 = ts_parse(str(p), "TS")
        assert r1[0]["id"] == r2[0]["id"]


# ═══ WEBULL PARSER ═══════════════════════════════════════════════════════════

class TestWebullInvParser:
    def _csv(self, tmp_path, rows):
        p = tmp_path / "wb.csv"
        if rows:
            df = pd.DataFrame(rows)
            df.to_csv(p, index=False)
        else:
            p.write_text("TYPE,DATE,AMOUNT,DESCRIPTION\n")
        return p

    def test_dividend_row(self, tmp_path):
        p = self._csv(tmp_path, [
            {"TYPE": "Dividends", "DATE": "04/01/2026",
             "AMOUNT": "$25.00", "DESCRIPTION": "AAPL dividend"}
        ])
        records = parse_inv(str(p), "WEBULL")
        assert len(records) == 1
        assert records[0]["category"] == "dividend"
        assert records[0]["amount"] == pytest.approx(25.0)

    def test_margin_loan_interest(self, tmp_path):
        p = self._csv(tmp_path, [
            {"TYPE": "Interest", "DATE": "04/01/2026",
             "AMOUNT": "-$10.50", "DESCRIPTION": "Margin loan interest"}
        ])
        records = parse_inv(str(p), "WEBULL")
        assert records[0]["category"] == "margin_interest"

    def test_non_margin_interest_is_reward(self, tmp_path):
        p = self._csv(tmp_path, [
            {"TYPE": "Interest", "DATE": "04/01/2026",
             "AMOUNT": "$1.00", "DESCRIPTION": "Securities lending rebate"}
        ])
        records = parse_inv(str(p), "WEBULL")
        assert records[0]["category"] == "reward"

    def test_deposit_transfer(self, tmp_path):
        p = self._csv(tmp_path, [
            {"TYPE": "Transfer", "DATE": "04/01/2026",
             "AMOUNT": "$5000.00", "DESCRIPTION": "Bank deposit"}
        ])
        records = parse_inv(str(p), "WEBULL")
        assert records[0]["subcategory"] == "deposit"

    def test_withdrawal_transfer(self, tmp_path):
        p = self._csv(tmp_path, [
            {"TYPE": "Transfer", "DATE": "04/01/2026",
             "AMOUNT": "-$2000.00", "DESCRIPTION": "Wire out"}
        ])
        records = parse_inv(str(p), "WEBULL")
        assert records[0]["subcategory"] == "withdrawal"

    def test_trade_rows_skipped(self, tmp_path):
        p = self._csv(tmp_path, [
            {"TYPE": "Trade", "DATE": "04/01/2026",
             "AMOUNT": "-$1000.00", "DESCRIPTION": "Buy AAPL"}
        ])
        assert parse_inv(str(p), "WEBULL") == []

    def test_option_exercise_skipped(self, tmp_path):
        p = self._csv(tmp_path, [
            {"TYPE": "Option Exercise", "DATE": "04/01/2026",
             "AMOUNT": "-$500.00", "DESCRIPTION": "Exercised"}
        ])
        assert parse_inv(str(p), "WEBULL") == []


class TestWebullCashParser:
    def _csv(self, tmp_path, rows):
        p = tmp_path / "wb_cash.csv"
        if rows:
            df = pd.DataFrame(rows)
            df.to_csv(p, index=False)
        else:
            p.write_text("TYPE,DATE,AMOUNT,DESCRIPTION\n")
        return p

    def test_interest_reward(self, tmp_path):
        p = self._csv(tmp_path, [
            {"TYPE": "Interest", "DATE": "04/01/2026",
             "AMOUNT": "$2.00", "DESCRIPTION": "Interest earned"}
        ])
        records = parse_cash(str(p), "WEBULL-CASH")
        assert records[0]["category"] == "reward"

    def test_platform_rewards(self, tmp_path):
        p = self._csv(tmp_path, [
            {"TYPE": "Platform Rewards", "DATE": "04/01/2026",
             "AMOUNT": "$5.00", "DESCRIPTION": "Reward"}
        ])
        records = parse_cash(str(p), "WEBULL-CASH")
        assert records[0]["category"] == "reward"
        assert records[0]["subcategory"] == "platform_reward"

    def test_fee(self, tmp_path):
        p = self._csv(tmp_path, [
            {"TYPE": "Fees", "DATE": "04/01/2026",
             "AMOUNT": "-$1.00", "DESCRIPTION": "Platform fee"}
        ])
        records = parse_cash(str(p), "WEBULL-CASH")
        assert records[0]["category"] == "fee"

    def test_deposit_transfer(self, tmp_path):
        p = self._csv(tmp_path, [
            {"TYPE": "Transfer", "DATE": "04/01/2026",
             "AMOUNT": "$1000.00", "DESCRIPTION": "Deposit"}
        ])
        records = parse_cash(str(p), "WEBULL-CASH")
        assert records[0]["subcategory"] == "deposit"


# ═══ FIDELITY PARSER ═════════════════════════════════════════════════════════

class TestFidelityYearDate:
    def test_plain_year(self):
        yr, date = _year_date("2025")
        assert yr == 2025
        assert date == "2025-12-31"

    def test_as_of_date(self):
        yr, date = _year_date("2026(As of Apr-23-2026)")
        assert yr == 2026
        assert date == "2026-04-23"

    def test_as_of_dec(self):
        yr, date = _year_date("2015(As of Dec-01-2015)")
        assert yr == 2015
        assert date == "2015-12-01"

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _year_date("Total")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            _year_date("")


class TestFidelityParser:
    HEADER_ROWS = [
        "Fidelity Investments",
        "Investment Income Report",
        "Jan 1 2020 - Apr 30 2026",
    ]
    COL_ROW = "Yearly,Beginning balance,Dividends,Interest,Capital gains,Deposits,Withdrawals,Ending balance"

    def _write(self, tmp_path, data_rows: list[str]) -> Path:
        lines = self.HEADER_ROWS + [self.COL_ROW] + data_rows
        p = tmp_path / "fidelity.csv"
        p.write_text("\n".join(lines), encoding="utf-8")
        return p

    def test_basic_dividend_row(self, tmp_path):
        p = self._write(tmp_path, ["2025,100000,1500.00,0,0,0,0,110000"])
        records = fidelity_parse(str(p), "FIDELITY")
        divs = [r for r in records if r["category"] == "dividend"]
        assert len(divs) == 1
        assert divs[0]["amount"] == pytest.approx(1500.0)

    def test_margin_interest_negative(self, tmp_path):
        p = self._write(tmp_path, ["2025,100000,0,-200.00,0,0,0,99800"])
        records = fidelity_parse(str(p), "FIDELITY")
        mi = [r for r in records if r["category"] == "margin_interest"]
        assert len(mi) == 1
        assert mi[0]["amount"] < 0

    def test_positive_interest_is_reward(self, tmp_path):
        p = self._write(tmp_path, ["2025,100000,0,50.00,0,0,0,100050"])
        records = fidelity_parse(str(p), "FIDELITY")
        rewards = [r for r in records if r["category"] == "reward"]
        assert len(rewards) == 1

    def test_deposits_captured(self, tmp_path):
        p = self._write(tmp_path, ["2025,0,0,0,0,10000,0,10000"])
        records = fidelity_parse(str(p), "FIDELITY")
        deps = [r for r in records if r["subcategory"] == "deposit"]
        assert len(deps) == 1
        assert deps[0]["amount"] == pytest.approx(10000.0)

    def test_withdrawals_are_negative(self, tmp_path):
        p = self._write(tmp_path, ["2025,50000,0,0,0,0,5000,45000"])
        records = fidelity_parse(str(p), "FIDELITY")
        wds = [r for r in records if r["subcategory"] == "withdrawal"]
        assert wds[0]["amount"] < 0

    def test_year_before_start_year_skipped(self, tmp_path):
        p = self._write(tmp_path, ["2019,50000,500,0,0,0,0,50500"])
        records = fidelity_parse(str(p), "FIDELITY")
        assert records == []

    def test_total_row_stops_parsing(self, tmp_path):
        p = self._write(tmp_path, [
            "2025,100000,1000,0,0,0,0,101000",
            "Total,,,,,,,",
            "2024,90000,800,0,0,0,0,91000",   # should be ignored
        ])
        records = fidelity_parse(str(p), "FIDELITY")
        years = {r["date"][:4] for r in records}
        assert "2024" not in years

    def test_zero_amounts_not_included(self, tmp_path):
        p = self._write(tmp_path, ["2025,100000,0,0,0,0,0,100000"])
        records = fidelity_parse(str(p), "FIDELITY")
        assert records == []

    def test_multiple_years(self, tmp_path):
        p = self._write(tmp_path, [
            "2025,100000,1500,0,0,5000,0,106500",
            "2024,90000,1200,0,0,0,2000,89200",
        ])
        records = fidelity_parse(str(p), "FIDELITY")
        years = {r["date"][:4] for r in records}
        assert "2025" in years
        assert "2024" in years

    def test_missing_columns_raises(self, tmp_path):
        # Must have 4 header rows (3 metadata + col header) for Fidelity skiprows=3
        lines = ["Report header", "Line 2", "Line 3", "Year,Nothing", "2025,0"]
        p = tmp_path / "bad.csv"
        p.write_text("\n".join(lines), encoding="utf-8")
        with pytest.raises(ValueError, match="missing expected columns"):
            fidelity_parse(str(p), "FIDELITY")
