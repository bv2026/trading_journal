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
    cf   = d[d["category"] == "cash_flow"]
    ext  = cf[cf["subcategory"] != "internal_transfer"]
    crypto = d[d["category"] == "crypto_flow"]
    return {
        "deposits":       float(ext[ext["amount"] > 0]["amount"].sum()),
        "withdrawals":    float(ext[ext["amount"] < 0]["amount"].sum()),
        "net_cash":       float(ext["amount"].sum()),
        "crypto_in":      float(crypto[crypto["amount"] > 0]["amount"].sum()),
        "crypto_out":     float(crypto[crypto["amount"] < 0]["amount"].sum()),
        "net_crypto":     float(crypto["amount"].sum()),
        "dividends":      float(d[d["category"] == "dividend"]["amount"].sum()),
        "rewards":        float(d[d["category"] == "reward"]["amount"].sum()),
        "margin_int":     float(d[d["category"] == "margin_interest"]["amount"].sum()),
        "fees":           float(d[d["category"] == "fee"]["amount"].sum()),
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
tab_acct, tab_yearly, tab_monthly, tab_txns = st.tabs([
    "By Account", "Yearly Summary", "Monthly Trends", "Transactions"
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
            "Net Cash":        am["net_cash"] + am["net_crypto"],
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
    def _net_cash(m): return m["net_cash"] + m["net_crypto"]
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
