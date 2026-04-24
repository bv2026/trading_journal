# -*- coding: utf-8 -*-
"""Portfolio Journal — Streamlit dashboard.
Run: streamlit run dashboard/app.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from src.db import DB_PATH, load_transactions

# ── Positions file path ─────────────────────────────────────────────────────────
POSITIONS_FILE = Path(__file__).parent.parent / "activity" / "TRADEPOSITIONS.xlsx"

# Sheet → account ID mapping (skip TLOG-RECONCILE)
_SHEET_ACCOUNT = {
    "SCWB":     "SCHWAB",
    "TRDER":    "TRADIER",
    "TRDSTN":   "TS",
    "RH-KD":    "RH-KD",
    "RH-BV":    "RH-BV",
    "WBULL":    "WEBULL",
    "FIDELITY": "FIDELITY",
}

_SKIP_COLS = {"Unnamed", "MS FORM"}  # prefixes to drop


@st.cache_data(ttl=300)
def _load_positions() -> pd.DataFrame:
    """Load all position sheets from TRADEPOSITIONS.xlsx into one DataFrame."""
    if not POSITIONS_FILE.exists():
        return pd.DataFrame()
    frames = []
    for sheet, acct in _SHEET_ACCOUNT.items():
        try:
            df_ = pd.read_excel(POSITIONS_FILE, sheet_name=sheet)
            # Drop helper/unnamed columns
            keep = [c for c in df_.columns
                    if not any(str(c).startswith(p) for p in _SKIP_COLS)]
            df_ = df_[keep].copy()
            # Standardise column names
            df_.rename(columns={
                "ATR %":    "ATR_pct",
                "IV RANK":  "IV_Rank",
                "PERF YTD": "PERF_YTD",
                "Sh/Contr": "Shares",
                "COST BASIS": "Cost_Basis",
            }, inplace=True)
            # Ensure Ticker is a string and drop blank rows
            df_["Ticker"] = df_["Ticker"].astype(str).str.strip()
            df_ = df_[df_["Ticker"] != "nan"]
            df_["Account"] = acct
            frames.append(df_)
        except Exception:
            pass
    if not frames:
        return pd.DataFrame()
    pos = pd.concat(frames, ignore_index=True)
    pos["sector"] = pos["sector"].fillna("Unknown")
    pos["industry"] = pos["industry"].fillna("Unknown")
    pos["TYPE"] = pos["TYPE"].fillna("Unknown")
    return pos

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Portfolio Journal", page_icon="📊", layout="wide")

# ── Colour palette ─────────────────────────────────────────────────────────────
C_GREEN  = "#22c55e"
C_RED    = "#ef4444"
C_ORANGE = "#f97316"
C_BLUE   = "#3b82f6"
C_PURPLE = "#a855f7"

ACCOUNT_COLOURS = px.colors.qualitative.Safe

# ── Load & cache ───────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def _load() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    return load_transactions()


df_all = _load()

if df_all.empty:
    st.error("No data found — run  `python ingest.py`  first.")
    st.stop()

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📊 Portfolio Journal")
    st.divider()

    min_d = df_all["date"].min().date()
    max_d = df_all["date"].max().date()

    date_range = st.date_input("Date range", value=(min_d, max_d),
                               min_value=min_d, max_value=max_d)
    start_d, end_d = date_range if len(date_range) == 2 else (min_d, max_d)

    all_accounts = sorted(df_all["account_id"].unique())
    accounts = st.multiselect("Accounts", all_accounts, default=all_accounts)

    show_internal = st.checkbox("Include internal transfers", value=False)

    st.divider()
    st.caption(f"DB: `{DB_PATH.name}`")
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

# ── Filter ─────────────────────────────────────────────────────────────────────
mask = (
    (df_all["date"].dt.date >= start_d)
    & (df_all["date"].dt.date <= end_d)
    & (df_all["account_id"].isin(accounts))
)
df = df_all[mask].copy()

if not show_internal:
    df = df[df["subcategory"] != "internal_transfer"]
df = df[df["category"] != "other"]

# ── Helpers ────────────────────────────────────────────────────────────────────
def _sum(d: pd.DataFrame, cat, sub=None) -> float:
    m = d["category"] == cat
    if sub:
        m &= d["subcategory"] == sub
    return float(d[m]["amount"].sum())


def _metrics(d: pd.DataFrame) -> dict:
    cf  = d[d["category"] == "cash_flow"]
    ext = cf[cf["subcategory"] != "internal_transfer"]

    # Bank/USD crypto_flow subcategories count toward net cash
    _BANK_SUBS = {"usd_deposit", "usd_withdrawal", "bank_purchase"}
    crypto     = d[d["category"] == "crypto_flow"]
    bank_crypto = crypto[crypto["subcategory"].isin(_BANK_SUBS)]

    # External wallet transfers (crypto_received / crypto_sent) shown separately
    wallet = crypto[~crypto["subcategory"].isin(_BANK_SUBS)]

    return {
        "deposits":    float(ext[ext["amount"] > 0]["amount"].sum())
                       + float(bank_crypto[bank_crypto["amount"] > 0]["amount"].sum()),
        "withdrawals": float(ext[ext["amount"] < 0]["amount"].sum())
                       + float(bank_crypto[bank_crypto["amount"] < 0]["amount"].sum()),
        "net_cash":    float(ext["amount"].sum()) + float(bank_crypto["amount"].sum()),
        "crypto_in":   float(wallet[wallet["amount"] > 0]["amount"].sum()),
        "crypto_out":  float(wallet[wallet["amount"] < 0]["amount"].sum()),
        "net_crypto":  float(wallet["amount"].sum()),
        "dividends":   float(d[d["category"] == "dividend"]["amount"].sum()),
        "rewards":     float(d[d["category"] == "reward"]["amount"].sum()),
        "margin_int":  float(d[d["category"] == "margin_interest"]["amount"].sum()),
        "fees":        float(d[d["category"] == "fee"]["amount"].sum()),
    }


def _net_income(m: dict) -> float:
    return m["dividends"] + m["rewards"] + m["margin_int"] + m["fees"]


def _fmt(v: float, parens: bool = False) -> str:
    if parens and v < 0:
        return f"(${abs(v):,.2f})"
    return f"${v:+,.2f}" if v != 0 else "$0.00"


def _colour_cell(v: float) -> str:
    return "color: #16a34a" if v >= 0 else "color: #dc2626"


def _style_table(df_: pd.DataFrame, money_cols: list[str]) -> object:
    fmt = {c: "${:,.2f}" for c in money_cols}
    return df_.style.format(fmt).map(_colour_cell, subset=money_cols)


# ── Header KPIs ────────────────────────────────────────────────────────────────
m_all = _metrics(df)
st.title("Portfolio Journal")
st.caption(f"{len(df):,} transactions · {start_d} → {end_d} · "
           f"{len(accounts)} account(s)")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Net Cash Flow",       _fmt(m_all["net_cash"]))
c2.metric("Dividends & Rewards", f"${m_all['dividends'] + m_all['rewards']:,.2f}")
c3.metric("Margin Interest",     _fmt(m_all["margin_int"]))
c4.metric("Fees",                _fmt(m_all["fees"]))
c5.metric("Net Income",          _fmt(_net_income(m_all)))

st.divider()

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab_acct, tab_yearly, tab_monthly, tab_txns, tab_pos = st.tabs([
    "By Account", "Yearly Summary", "Monthly Trends", "Transactions", "Positions"
])


# ═══ TAB 1 — By Account ═══════════════════════════════════════════════════════
with tab_acct:

    df_acct = df.copy()
    df_acct["year"] = df_acct["date"].dt.year
    acct_years = sorted(df_acct["year"].dropna().unique().astype(int))

    def _pivot(source: pd.DataFrame, metric_fn, label: str) -> pd.DataFrame:
        """Build Account × Year pivot with an ALL column, rows, and totals."""
        rows = {}
        for acct in [a for a in all_accounts if a in accounts]:
            ad = source[source["account_id"] == acct]
            rows[acct] = {yr: metric_fn(_metrics(ad[ad["year"] == yr]))
                          for yr in acct_years}
            rows[acct]["ALL"] = metric_fn(_metrics(ad))
        pv = pd.DataFrame(rows).T.reset_index().rename(columns={"index": "Account"})
        # totals row
        totals = {"Account": "TOTAL"}
        for yr in acct_years:
            totals[yr] = metric_fn(_metrics(source[source["year"] == yr]))
        totals["ALL"] = metric_fn(_metrics(source))
        pv = pd.concat([pv, pd.DataFrame([totals])], ignore_index=True)
        cols = ["Account"] + acct_years + ["ALL"]
        return pv[cols]

    def _show_pivot(pv: pd.DataFrame, title: str):
        yr_cols = [c for c in pv.columns if c != "Account"]
        st.subheader(title)
        st.dataframe(_style_table(pv, yr_cols), use_container_width=True, hide_index=True)

    # ── Account Summary (all-time) ─────────────────────────────────────────────
    rows = []
    for acct in [a for a in all_accounts if a in accounts]:
        am = _metrics(df[df["account_id"] == acct])
        row = {
            "Account":         acct,
            "Broker":          df[df["account_id"] == acct]["broker"].iloc[0],
            "Net Cash":        am["net_cash"],
            "Dividends":       am["dividends"],
            "Rewards":         am["rewards"],
            "Margin Interest": am["margin_int"],
            "Fees":            am["fees"],
            "Net Income":      _net_income(am),
        }
        rows.append(row)
    tbl = pd.DataFrame(rows).fillna(0)
    money_cols = ["Net Cash", "Dividends", "Rewards", "Margin Interest", "Fees", "Net Income"]
    st.subheader("Account Summary")
    st.dataframe(_style_table(tbl, money_cols), use_container_width=True, hide_index=True)

    st.divider()

    # ── Net Cash Flow by Account × Year ───────────────────────────────────────
    def _net_cash(m): return m["net_cash"]
    pv_cash = _pivot(df_acct, _net_cash, "Net Cash Flow")
    _show_pivot(pv_cash, "Net Cash Flow by Account & Year")

    st.divider()

    # ── Dividends by Account × Year ───────────────────────────────────────────
    pv_div = _pivot(df_acct, lambda m: m["dividends"], "Dividends")
    _show_pivot(pv_div, "Dividends by Account & Year")

    st.divider()

    # ── Rewards by Account × Year ─────────────────────────────────────────────
    pv_rew = _pivot(df_acct, lambda m: m["rewards"], "Rewards")
    _show_pivot(pv_rew, "Rewards by Account & Year")

    st.divider()

    # ── Margin Interest by Account × Year ─────────────────────────────────────
    pv_marg = _pivot(df_acct, lambda m: m["margin_int"], "Margin Interest")
    _show_pivot(pv_marg, "Margin Interest by Account & Year")

    st.divider()

    # ── Fees by Account × Year ────────────────────────────────────────────────
    pv_fee = _pivot(df_acct, lambda m: m["fees"], "Fees")
    _show_pivot(pv_fee, "Fees by Account & Year")

    # ── Crypto Flow detail (Coinbase) ─────────────────────────────────────────
    crypto_df = df[df["category"] == "crypto_flow"]
    if not crypto_df.empty:
        st.divider()
        st.subheader("Crypto Flow — Coinbase (external movements only)")

        total_in  = float(crypto_df[crypto_df["amount"] > 0]["amount"].sum())
        total_out = float(crypto_df[crypto_df["amount"] < 0]["amount"].sum())
        net       = total_in + total_out

        INFLOW_SUBS  = ["usd_deposit", "bank_purchase", "crypto_received"]
        OUTFLOW_SUBS = ["usd_withdrawal", "crypto_sent"]
        LABELS = {
            "usd_deposit":    "USD Deposited (direct)",
            "bank_purchase":  "Bought Crypto via Bank / PayPal",
            "crypto_received":"Crypto Received (external wallet)",
            "usd_withdrawal": "USD Withdrawn",
            "crypto_sent":    "Crypto Sent (external wallet)",
        }

        col_in, col_out, col_net = st.columns(3)

        with col_in:
            st.markdown("**Inflows**")
            in_rows = []
            for sub in INFLOW_SUBS:
                v = float(crypto_df[crypto_df["subcategory"] == sub]["amount"].sum())
                n = int((crypto_df["subcategory"] == sub).sum())
                in_rows.append({"Type": LABELS[sub], "Amount": v, "Txns": n})
            in_df = pd.DataFrame(in_rows)
            in_df.loc[len(in_df)] = ["Total In", total_in, ""]
            st.dataframe(in_df.style.format({"Amount": "${:,.2f}"}),
                         use_container_width=True, hide_index=True)

        with col_out:
            st.markdown("**Outflows**")
            out_rows = []
            for sub in OUTFLOW_SUBS:
                v = float(crypto_df[crypto_df["subcategory"] == sub]["amount"].sum())
                n = int((crypto_df["subcategory"] == sub).sum())
                out_rows.append({"Type": LABELS[sub], "Amount": v, "Txns": n})
            out_df = pd.DataFrame(out_rows)
            out_df.loc[len(out_df)] = ["Total Out", total_out, ""]
            st.dataframe(out_df.style.format({"Amount": "${:,.2f}"}),
                         use_container_width=True, hide_index=True)

        with col_net:
            st.markdown("**Net**")
            st.metric("Total In",  f"${total_in:,.2f}")
            st.metric("Total Out", f"${total_out:,.2f}")
            st.metric("Net Cash",  f"${net:,.2f}", delta=f"${net:,.2f}")


# ═══ TAB 2 — Yearly Summary ═══════════════════════════════════════════════════
with tab_yearly:
    df["year"] = df["date"].dt.year
    years = sorted(df["year"].dropna().unique().astype(int))

    # ── Year-over-year table ───────────────────────────────────────────────────
    st.subheader("Year-over-Year Summary")
    yr_rows = []
    for yr in years:
        yd  = df[df["year"] == yr]
        ym  = _metrics(yd)
        yr_rows.append({
            "Year":            int(yr),
            "Deposits":        ym["deposits"],
            "Withdrawals":     ym["withdrawals"],
            "Net Cash":        ym["net_cash"],
            "Dividends":       ym["dividends"],
            "Rewards":         ym["rewards"],
            "Div + Rewards":   ym["dividends"] + ym["rewards"],
            "Margin Interest": ym["margin_int"],
            "Fees":            ym["fees"],
            "Net Income":      _net_income(ym),
        })

    # Totals row
    t = _metrics(df)
    yr_rows.append({
        "Year":            "ALL",
        "Deposits":        t["deposits"],
        "Withdrawals":     t["withdrawals"],
        "Net Cash":        t["net_cash"],
        "Dividends":       t["dividends"],
        "Rewards":         t["rewards"],
        "Div + Rewards":   t["dividends"] + t["rewards"],
        "Margin Interest": t["margin_int"],
        "Fees":            t["fees"],
        "Net Income":      _net_income(t),
    })

    yr_df = pd.DataFrame(yr_rows)
    yr_money = ["Deposits", "Withdrawals", "Net Cash", "Dividends", "Rewards",
                "Div + Rewards", "Margin Interest", "Fees", "Net Income"]
    st.dataframe(_style_table(yr_df, yr_money), use_container_width=True, hide_index=True)

    st.divider()

    # ── Year charts ────────────────────────────────────────────────────────────
    yr_plot = pd.DataFrame([r for r in yr_rows if r["Year"] != "ALL"])

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Income vs Costs by Year")
        fig = go.Figure()
        fig.add_bar(name="Dividends",      x=yr_plot["Year"], y=yr_plot["Dividends"],
                    marker_color=C_GREEN)
        fig.add_bar(name="Rewards",        x=yr_plot["Year"], y=yr_plot["Rewards"],
                    marker_color=C_BLUE)
        fig.add_bar(name="Margin Interest",x=yr_plot["Year"], y=yr_plot["Margin Interest"],
                    marker_color=C_RED)
        fig.add_bar(name="Fees",           x=yr_plot["Year"], y=yr_plot["Fees"],
                    marker_color=C_ORANGE)
        fig.update_layout(barmode="relative",
                          xaxis=dict(type="category"),
                          yaxis_title="USD",
                          legend=dict(orientation="h", y=-0.2),
                          margin=dict(t=10))
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Net Income by Year")
        yr_plot2 = yr_plot.copy()
        yr_plot2["colour"] = yr_plot2["Net Income"].apply(
            lambda v: "Positive" if v >= 0 else "Negative"
        )
        fig = px.bar(yr_plot2, x="Year", y="Net Income", color="colour",
                     color_discrete_map={"Positive": C_GREEN, "Negative": C_RED},
                     labels={"Net Income": "USD"})
        fig.update_layout(showlegend=False, xaxis=dict(type="category"),
                          margin=dict(t=10))
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Cash Flow by Year")
    col3, col4 = st.columns(2)

    with col3:
        fig = go.Figure()
        fig.add_bar(name="Deposits",     x=yr_plot["Year"], y=yr_plot["Deposits"],
                    marker_color=C_GREEN)
        fig.add_bar(name="Withdrawals",  x=yr_plot["Year"], y=yr_plot["Withdrawals"],
                    marker_color=C_RED)
        fig.update_layout(barmode="group", xaxis=dict(type="category"),
                          yaxis_title="USD",
                          legend=dict(orientation="h", y=-0.2),
                          margin=dict(t=10))
        st.plotly_chart(fig, use_container_width=True)

    with col4:
        # Dividend growth by year — stacked by account
        st.subheader("Dividends by Year & Account")
        div_yr = (
            df[df["category"].isin(["dividend", "reward"]) & (df["amount"] > 0)]
            .groupby(["year", "account_id"])["amount"].sum().reset_index()
        )
        if not div_yr.empty:
            fig = px.bar(div_yr, x="year", y="amount", color="account_id",
                         barmode="stack",
                         labels={"amount": "USD", "year": "Year", "account_id": "Account"},
                         color_discrete_sequence=ACCOUNT_COLOURS)
            fig.update_layout(xaxis=dict(type="category"),
                              legend_title="Account",
                              margin=dict(t=10))
            st.plotly_chart(fig, use_container_width=True)

    # ── Drilldown by year ──────────────────────────────────────────────────────
    st.divider()
    st.subheader("Drilldown — Income by Type per Year")
    inc_yr = (
        df[df["category"].isin(["dividend", "reward"]) & (df["amount"] > 0)]
        .groupby(["year", "subcategory"])["amount"].sum()
        .unstack(fill_value=0)
        .reset_index()
    )
    if not inc_yr.empty:
        sub_cols = [c for c in inc_yr.columns if c != "year"]
        inc_yr_fmt = inc_yr.rename(columns={"year": "Year"})
        st.dataframe(
            inc_yr_fmt.style.format({c: "${:,.2f}" for c in sub_cols}),
            use_container_width=True, hide_index=True,
        )


# ═══ TAB 3 — Monthly Trends ════════════════════════════════════════════════════
with tab_monthly:
    monthly = df.copy()
    monthly["month"] = monthly["date"].dt.to_period("M").dt.to_timestamp()

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Dividends & Rewards by Month")
        inc = monthly[monthly["category"].isin(["dividend", "reward"]) & (monthly["amount"] > 0)]
        if not inc.empty:
            agg = inc.groupby(["month", "account_id"])["amount"].sum().reset_index()
            fig = px.bar(agg, x="month", y="amount", color="account_id",
                         barmode="stack",
                         labels={"amount": "USD", "month": "", "account_id": "Account"},
                         color_discrete_sequence=ACCOUNT_COLOURS)
            fig.update_layout(legend_title="Account", margin=dict(t=10))
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Costs by Month")
        costs = monthly[monthly["category"].isin(["margin_interest", "fee"])]
        if not costs.empty:
            agg = costs.groupby(["month", "category"])["amount"].sum().abs().reset_index()
            fig = px.bar(agg, x="month", y="amount", color="category",
                         barmode="stack",
                         labels={"amount": "USD", "month": "", "category": "Type"},
                         color_discrete_map={"margin_interest": C_RED, "fee": C_ORANGE})
            fig.update_layout(margin=dict(t=10))
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("Net Cash Flow by Month")
    cf = monthly[monthly["category"] == "cash_flow"]
    if not cf.empty:
        agg = cf.groupby("month")["amount"].sum().reset_index()
        agg["dir"] = agg["amount"].apply(lambda x: "Net In" if x >= 0 else "Net Out")
        fig = px.bar(agg, x="month", y="amount", color="dir",
                     labels={"amount": "USD", "month": ""},
                     color_discrete_map={"Net In": C_GREEN, "Net Out": C_RED})
        fig.update_layout(showlegend=True, margin=dict(t=10))
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Cumulative Income vs Costs")
    inc_m  = monthly[monthly["category"].isin(["dividend", "reward"]) & (monthly["amount"] > 0)].groupby("month")["amount"].sum()
    cost_m = monthly[monthly["category"].isin(["margin_interest", "fee"])].groupby("month")["amount"].sum().abs()
    cum_df = pd.DataFrame({"Income": inc_m, "Costs": cost_m}).fillna(0).cumsum().reset_index()
    if not cum_df.empty:
        fig = px.line(cum_df, x="month", y=["Income", "Costs"],
                      labels={"value": "USD", "month": "", "variable": ""},
                      color_discrete_map={"Income": C_GREEN, "Costs": C_RED})
        fig.update_layout(margin=dict(t=10))
        st.plotly_chart(fig, use_container_width=True)


# ═══ TAB 4 — Transactions ══════════════════════════════════════════════════════
with tab_txns:
    col1, col2, col3, col4 = st.columns([2, 2, 2, 3])
    with col1:
        cat_filter = st.multiselect("Category",
                                    sorted(df["category"].unique()),
                                    default=sorted(df["category"].unique()))
    with col2:
        acct_filter = st.multiselect("Account",
                                     sorted(df["account_id"].unique()),
                                     default=sorted(df["account_id"].unique()))
    with col3:
        yr_filter = st.multiselect("Year",
                                   sorted(df["date"].dt.year.unique().astype(int), reverse=True),
                                   default=[])
    with col4:
        search = st.text_input("Search description",
                               placeholder="AAPL, margin, staking …")

    txn = df[df["category"].isin(cat_filter) & df["account_id"].isin(acct_filter)].copy()
    if yr_filter:
        txn = txn[txn["date"].dt.year.isin(yr_filter)]
    if search:
        txn = txn[txn["description"].str.contains(search, case=False, na=False)]

    display_cols = ["date", "account_id", "broker", "category", "subcategory",
                    "amount", "currency", "symbol", "description"]
    st.caption(f"{len(txn):,} rows")
    st.dataframe(
        txn.sort_values("date", ascending=False)[display_cols].reset_index(drop=True),
        use_container_width=True,
        column_config={
            "date":   st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
            "amount": st.column_config.NumberColumn("Amount", format="$%.2f"),
        },
    )
    st.download_button("⬇ Download CSV",
                       txn[display_cols].to_csv(index=False).encode(),
                       "transactions.csv", "text/csv")


# ═══ TAB 5 — Positions ═════════════════════════════════════════════════════════
with tab_pos:
    pos_all = _load_positions()

    if pos_all.empty:
        st.warning(f"Positions file not found at `{POSITIONS_FILE}`.  "
                   "Place `TRADEPOSITIONS.xlsx` in the `activity/` folder and refresh.")
        st.stop()

    # ── Split MARGIN rows and coerce numerics ──────────────────────────────────
    margin_df = pos_all[pos_all["Ticker"] == "MARGIN"].copy()
    pos = pos_all[pos_all["Ticker"] != "MARGIN"].copy()

    for col in ["PRICE", "Shares", "Cost_Basis", "COST", "MARKET VALUE",
                "totalReturn", "IV_Rank", "PERF_YTD", "ATR_pct"]:
        if col in pos.columns:
            pos[col] = pd.to_numeric(pos[col], errors="coerce")
    margin_df["MARKET VALUE"] = pd.to_numeric(
        margin_df["MARKET VALUE"], errors="coerce"
    )

    # ── fmt helpers (positions-specific) ──────────────────────────────────────
    _pos_fmt = {
        "PRICE":        "${:.2f}",
        "Cost_Basis":   "${:.4f}",
        "COST":         "${:,.2f}",
        "MARKET VALUE": "${:,.2f}",
        "totalReturn":  "${:+,.2f}",
        "Return_%":     "{:+.2f}%",
        "PERF_YTD":     "{:.2%}",
        "ATR_pct":      "{:.2%}",
        "IV_Rank":      "{:.2%}",
        "Shares":       "{:,.4f}",
    }

    # ── 1. ACCOUNT SUMMARY TABLE ───────────────────────────────────────────────
    # Positions side: MV, Cost, P&L
    pos_by_acct = (
        pos.groupby("Account")
           .agg(Positions  =("Ticker",       "count"),
                Market_Value=("MARKET VALUE", "sum"),
                Total_Cost  =("COST",         "sum"),
                PnL         =("totalReturn",  "sum"))
           .reset_index()
    )

    # Margin per account (already negative in the Excel)
    margin_by_acct = (
        margin_df.groupby("Account")["MARKET VALUE"]
                 .sum()
                 .reset_index()
                 .rename(columns={"MARKET VALUE": "Margin"})
    )

    # Fees & Dividends from the transaction DB — all-time, no date filter
    fees_by_acct = (
        df_all[df_all["category"] == "fee"]
        .groupby("account_id")["amount"].sum()
        .reset_index()
        .rename(columns={"account_id": "Account", "amount": "Fees"})
    )
    div_by_acct = (
        df_all[df_all["category"] == "dividend"]
        .groupby("account_id")["amount"].sum()
        .reset_index()
        .rename(columns={"account_id": "Account", "amount": "Dividends"})
    )

    acct_tbl = (
        pos_by_acct
        .merge(margin_by_acct, on="Account", how="left")
        .merge(fees_by_acct,   on="Account", how="left")
        .merge(div_by_acct,    on="Account", how="left")
    )
    acct_tbl["Margin"]    = acct_tbl["Margin"].fillna(0)
    acct_tbl["Fees"]      = acct_tbl["Fees"].fillna(0)
    acct_tbl["Dividends"] = acct_tbl["Dividends"].fillna(0)
    acct_tbl["Return_%"]  = (
        acct_tbl["PnL"] / acct_tbl["Total_Cost"] * 100
    ).round(2)
    acct_tbl = acct_tbl.sort_values("Market_Value", ascending=False)

    # Totals row
    _t = {
        "Account":      "TOTAL",
        "Positions":    int(acct_tbl["Positions"].sum()),
        "Market_Value": acct_tbl["Market_Value"].sum(),
        "Total_Cost":   acct_tbl["Total_Cost"].sum(),
        "PnL":          acct_tbl["PnL"].sum(),
        "Margin":       acct_tbl["Margin"].sum(),
        "Fees":         acct_tbl["Fees"].sum(),
        "Dividends":    acct_tbl["Dividends"].sum(),
    }
    _t["Return_%"] = (_t["PnL"] / _t["Total_Cost"] * 100) if _t["Total_Cost"] else 0
    acct_tbl = pd.concat(
        [acct_tbl, pd.DataFrame([_t])], ignore_index=True
    )

    disp_cols = ["Account", "Positions", "Market_Value", "Total_Cost",
                 "PnL", "Return_%", "Margin", "Fees", "Dividends"]
    acct_fmt = {
        "Market_Value": "${:,.2f}",
        "Total_Cost":   "${:,.2f}",
        "PnL":          "${:+,.2f}",
        "Return_%":     "{:+.2f}%",
        "Margin":       "${:,.2f}",
        "Fees":         "${:,.2f}",
        "Dividends":    "${:,.2f}",
    }

    st.subheader("Account Summary")
    st.dataframe(
        acct_tbl[disp_cols].style
            .format(acct_fmt)
            .map(_colour_cell, subset=["PnL", "Return_%", "Fees", "Dividends"]),
        use_container_width=True,
        hide_index=True,
    )

    st.divider()

    # ── 2. SECTOR ALLOCATION ───────────────────────────────────────────────────
    total_mv = pos["MARKET VALUE"].sum()

    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("Sector Allocation")
        sec_grp = (
            pos.groupby("sector")["MARKET VALUE"].sum()
               .sort_values(ascending=False)
               .reset_index()
        )
        fig_sec = px.pie(
            sec_grp, values="MARKET VALUE", names="sector",
            hole=0.4, color_discrete_sequence=px.colors.qualitative.Safe,
        )
        fig_sec.update_traces(
            textposition="inside", textinfo="percent+label",
            hovertemplate="%{label}<br>$%{value:,.0f}<br>%{percent}",
        )
        fig_sec.update_layout(showlegend=False, margin=dict(t=10, b=10))
        st.plotly_chart(fig_sec, use_container_width=True)

    with col_r:
        st.subheader("Account Allocation")
        acct_grp = (
            pos.groupby("Account")["MARKET VALUE"].sum()
               .sort_values(ascending=False)
               .reset_index()
        )
        fig_acct = px.pie(
            acct_grp, values="MARKET VALUE", names="Account",
            hole=0.4, color_discrete_sequence=ACCOUNT_COLOURS,
        )
        fig_acct.update_traces(
            textposition="inside", textinfo="percent+label",
            hovertemplate="%{label}<br>$%{value:,.0f}<br>%{percent}",
        )
        fig_acct.update_layout(showlegend=False, margin=dict(t=10, b=10))
        st.plotly_chart(fig_acct, use_container_width=True)

    # Sector summary table
    sec_tbl = (
        pos.groupby("sector")
           .agg(Positions   =("Ticker",       "count"),
                Market_Value=("MARKET VALUE", "sum"),
                Total_Cost  =("COST",         "sum"),
                PnL         =("totalReturn",  "sum"))
           .reset_index()
           .sort_values("Market_Value", ascending=False)
    )
    sec_tbl["Alloc_%"]  = (sec_tbl["Market_Value"] / total_mv * 100).round(2)
    sec_tbl["Return_%"] = (sec_tbl["PnL"] / sec_tbl["Total_Cost"] * 100).round(2)

    st.subheader("Sector Summary")
    st.dataframe(
        sec_tbl.style
            .format({"Market_Value": "${:,.2f}", "Total_Cost": "${:,.2f}",
                     "PnL": "${:+,.2f}", "Alloc_%": "{:.2f}%", "Return_%": "{:+.2f}%"})
            .map(_colour_cell, subset=["PnL", "Return_%"]),
        use_container_width=True,
        hide_index=True,
    )

    st.divider()

    # ── 3. POSITIONS BY ACCOUNT ────────────────────────────────────────────────
    st.subheader("Positions by Account")

    # Column order for per-account tables (skip Account col — redundant)
    _pos_cols = ["Ticker", "Name", "TYPE", "sector", "Shares",
                 "PRICE", "Cost_Basis", "COST", "MARKET VALUE",
                 "totalReturn", "Return_%", "PERF_YTD", "IV_Rank", "ATR_pct"]

    # Iterate accounts in descending MV order (same as summary table, excl TOTAL)
    acct_order = (
        acct_tbl[acct_tbl["Account"] != "TOTAL"]
        .sort_values("Market_Value", ascending=False)["Account"]
        .tolist()
    )

    for acct in acct_order:
        acct_pos = pos[pos["Account"] == acct].copy()
        if acct_pos.empty:
            continue

        acct_mv  = acct_pos["MARKET VALUE"].sum()
        acct_pnl = acct_pos["totalReturn"].sum()
        acct_ret = acct_pnl / acct_pos["COST"].sum() * 100 if acct_pos["COST"].sum() else 0
        acct_margin = float(
            margin_df[margin_df["Account"] == acct]["MARKET VALUE"].sum()
        )
        label = (
            f"**{acct}** — {len(acct_pos)} positions · "
            f"MV ${acct_mv:,.0f} · "
            f"P&L ${acct_pnl:+,.0f} ({acct_ret:+.1f}%) · "
            f"Margin ${abs(acct_margin):,.0f}"
        )
        with st.expander(label, expanded=False):
            acct_pos["Return_%"] = (
                acct_pos["totalReturn"] / acct_pos["COST"] * 100
            ).round(2)
            show = [c for c in _pos_cols if c in acct_pos.columns]
            fmt  = {k: v for k, v in _pos_fmt.items() if k in show}
            st.dataframe(
                acct_pos[show]
                    .sort_values("MARKET VALUE", ascending=False)
                    .reset_index(drop=True)
                    .style.format(fmt)
                    .map(_colour_cell, subset=["totalReturn", "Return_%"]),
                use_container_width=True,
                hide_index=True,
            )
