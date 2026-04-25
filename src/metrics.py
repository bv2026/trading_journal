# -*- coding: utf-8 -*-
"""Pure financial metric helpers — no Streamlit dependency.

Extracted from dashboard/app.py so the calculation logic can be unit-tested
independently of the Streamlit runtime.
"""
import pandas as pd

# Crypto subcategories that represent real bank / USD money movements.
# These count toward net cash; the remaining crypto subcategories
# (crypto_received, crypto_sent) are external wallet transfers shown separately.
BANK_CRYPTO_SUBS: frozenset[str] = frozenset({
    "usd_deposit",
    "usd_withdrawal",
    "bank_purchase",
})


def compute_metrics(df: pd.DataFrame) -> dict:
    """Compute portfolio metrics from a transaction DataFrame.

    Returns a dict with the following float keys:
        deposits, withdrawals, net_cash,
        crypto_in, crypto_out, net_crypto,
        dividends, rewards, margin_int, fees
    """
    cf  = df[df["category"] == "cash_flow"]
    ext = cf[cf["subcategory"] != "internal_transfer"]

    crypto      = df[df["category"] == "crypto_flow"]
    bank_crypto = crypto[crypto["subcategory"].isin(BANK_CRYPTO_SUBS)]
    wallet      = crypto[~crypto["subcategory"].isin(BANK_CRYPTO_SUBS)]

    return {
        "deposits":    (float(ext[ext["amount"] > 0]["amount"].sum())
                        + float(bank_crypto[bank_crypto["amount"] > 0]["amount"].sum())),
        "withdrawals": (float(ext[ext["amount"] < 0]["amount"].sum())
                        + float(bank_crypto[bank_crypto["amount"] < 0]["amount"].sum())),
        "net_cash":    float(ext["amount"].sum()) + float(bank_crypto["amount"].sum()),
        "crypto_in":   float(wallet[wallet["amount"] > 0]["amount"].sum()),
        "crypto_out":  float(wallet[wallet["amount"] < 0]["amount"].sum()),
        "net_crypto":  float(wallet["amount"].sum()),
        "dividends":   float(df[df["category"] == "dividend"]["amount"].sum()),
        "rewards":     float(df[df["category"] == "reward"]["amount"].sum()),
        "margin_int":  float(df[df["category"] == "margin_interest"]["amount"].sum()),
        "fees":        float(df[df["category"] == "fee"]["amount"].sum()),
    }


def net_income(m: dict) -> float:
    """Dividends + rewards + margin interest + fees (costs are negative)."""
    return m["dividends"] + m["rewards"] + m["margin_int"] + m["fees"]


def colour_cell(v) -> str:
    """Return a CSS colour string for a table cell value.

    * Green  (#16a34a) — zero or positive
    * Red    (#dc2626) — negative
    * Empty string    — NaN, None, or any non-numeric value (no colouring)
    """
    try:
        fv = float(v)
        if pd.isna(fv):
            return ""
        return "color: #16a34a" if fv >= 0 else "color: #dc2626"
    except (TypeError, ValueError):
        return ""


def _bold_last_row(row, last_idx) -> list[str]:
    style = "font-weight: bold; border-top: 2px solid #6b7280"
    return [style] * len(row) if row.name == last_idx else [""] * len(row)


def style_table(df_: pd.DataFrame, money_cols: list[str]) -> "pd.io.formats.style.Styler":
    """Format *money_cols* as $#,##0.00, apply colour coding, and bold the last row.

    Silently ignores column names that are not present in *df_* so callers
    do not need to pre-filter the list.
    """
    existing = [c for c in money_cols if c in df_.columns]
    fmt = {c: "${:,.2f}" for c in existing}
    styler = df_.style.format(fmt).map(colour_cell, subset=existing)
    if len(df_) > 0:
        styler = styler.apply(_bold_last_row, last_idx=df_.index[-1], axis=1)
    return styler
