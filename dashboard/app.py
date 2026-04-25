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
from src.metrics import compute_metrics, net_income as _net_income_fn, colour_cell, style_table, _bold_last_row
from src.positions import load_positions, compute_net_worth

# ── Positions file path ─────────────────────────────────────────────────────────
POSITIONS_FILE = Path(__file__).parent.parent / "activity" / "TRADEPOSITIONS.xlsx"


@st.cache_data(ttl=300)
def _load_positions() -> pd.DataFrame:
    """Streamlit-cached wrapper around src.positions.load_positions."""
    return load_positions(POSITIONS_FILE)

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
    st.error("No data found — run `python ingest.py` first.")
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
    if not accounts:
        accounts = all_accounts  # fall back to all when nothing selected

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

# ── Header KPIs ────────────────────────────────────────────────────────────────
# Metric / styling helpers live in src.metrics (imported at top of file).
m_all = compute_metrics(df)
st.title("Portfolio Journal")
st.caption(f"{len(df):,} transactions · {start_d} → {end_d} · "
           f"{len(accounts)} account(s)")

# ── Net Worth banner ───────────────────────────────────────────────────────────
try:
    _pos_hdr = _load_positions()
    if not _pos_hdr.empty and "MARKET VALUE" in _pos_hdr.columns:
        _pos_hdr_clean = _pos_hdr[_pos_hdr["Ticker"] != "MARGIN"].copy()
        _margin_hdr    = _pos_hdr[_pos_hdr["Ticker"] == "MARGIN"].copy()
        _pos_hdr_clean["MARKET VALUE"] = pd.to_numeric(
            _pos_hdr_clean["MARKET VALUE"], errors="coerce"
        )
        _margin_hdr["MARKET VALUE"] = pd.to_numeric(
            _margin_hdr["MARKET VALUE"], errors="coerce"
        )
        _total_mv     = float(_pos_hdr_clean["MARKET VALUE"].sum())
        _total_margin = abs(float(_margin_hdr["MARKET VALUE"].sum()))
        _net_worth    = _total_mv - _total_margin

        nw1, nw2, nw3 = st.columns(3)
        nw1.metric("Net Worth",       f"${_net_worth:,.0f}")
        nw2.metric("Market Value",    f"${_total_mv:,.0f}")
        nw3.metric("Margin Borrowed", f"${_total_margin:,.0f}",
                   delta=f"-${_total_margin:,.0f}", delta_color="inverse")
except Exception:
    pass  # Net worth banner is best-effort; failures must not crash the page

# ── Summary table (replaces individual metric widgets) ─────────────────────────
_kpi_row = {
    "Net Cash Flow":     m_all["net_cash"],
    "Dividends":         m_all["dividends"],
    "Rewards":           m_all["rewards"],
    "Div + Rewards":     m_all["dividends"] + m_all["rewards"],
    "Margin Interest":   m_all["margin_int"],
    "Fees":              m_all["fees"],
    "Net Income":        _net_income_fn(m_all),
}
_kpi_df = pd.DataFrame([_kpi_row])
st.dataframe(
    _kpi_df.style
        .format({c: "${:,.2f}" for c in _kpi_df.columns})
        .map(colour_cell, subset=list(_kpi_df.columns)),
    use_container_width=True,
    hide_index=True,
)

st.divider()

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab_portfolio, tab_yearly, tab_breakdown, tab_positions, tab_txns = st.tabs([
    "Portfolio", "Yearly Summary", "By Account", "Positions", "Transactions"
])


# ═══ TAB 1 — Portfolio ════════════════════════════════════════════════════════
with tab_portfolio:

    # ── Load positions data ────────────────────────────────────────────────────
    pos_all = _load_positions()
    has_positions = not pos_all.empty

    if has_positions:
        margin_df = pos_all[pos_all["Ticker"] == "MARGIN"].copy()
        pos = pos_all[pos_all["Ticker"] != "MARGIN"].copy()
        for col in ["PRICE", "Shares", "Cost_Basis", "COST", "MARKET VALUE",
                    "totalReturn", "IV_Rank", "PERF_YTD", "ATR_pct"]:
            if col in pos.columns:
                pos[col] = pd.to_numeric(pos[col], errors="coerce")
        margin_df["MARKET VALUE"] = pd.to_numeric(
            margin_df["MARKET VALUE"], errors="coerce"
        )
        pos_by_acct = (
            pos.groupby("Account")
               .agg(Positions   =("Ticker",       "count"),
                    Market_Value=("MARKET VALUE", "sum"),
                    Total_Cost  =("COST",         "sum"),
                    PnL         =("totalReturn",  "sum"))
               .reset_index()
        )
        margin_by_acct = (
            margin_df.groupby("Account")["MARKET VALUE"].sum()
                     .reset_index()
                     .rename(columns={"MARKET VALUE": "Margin"})
        )

    # ── 1. UNIFIED ACCOUNT SUMMARY ─────────────────────────────────────────────
    tx_rows = []
    for acct in [a for a in all_accounts if a in accounts]:
        ad = df[df["account_id"] == acct]
        if ad.empty:
            continue
        am = compute_metrics(ad)
        tx_rows.append({
            "Account":      acct,
            "Broker":       ad["broker"].iloc[0],
            "Net Cash":     am["net_cash"],
            "Dividends":    am["dividends"],
            "Rewards":      am["rewards"],
            "Margin Int":   am["margin_int"],
            "Fees":         am["fees"],
            "Net Income":   _net_income_fn(am),
        })
    summary = pd.DataFrame(tx_rows).fillna(0)

    if has_positions:
        summary = (
            summary
            .merge(pos_by_acct,    left_on="Account", right_on="Account", how="left")
            .merge(margin_by_acct, on="Account", how="left")
        )
        summary["Positions"]    = summary["Positions"].fillna(0).astype(int)
        summary["Market_Value"] = summary["Market_Value"].fillna(0)
        summary["Total_Cost"]   = summary["Total_Cost"].fillna(0)
        summary["PnL"]          = summary["PnL"].fillna(0)
        summary["Margin"]       = summary["Margin"].fillna(0)
        summary["Return_%"]     = (
            summary["PnL"] / summary["Total_Cost"].replace(0, float("nan")) * 100
        ).fillna(0).round(2)

        # Totals row
        _t = {
            "Account":      "TOTAL",
            "Broker":       "",
            "Positions":    int(summary["Positions"].sum()),
            "Market_Value": summary["Market_Value"].sum(),
            "Total_Cost":   summary["Total_Cost"].sum(),
            "PnL":          summary["PnL"].sum(),
            "Return_%":     (summary["PnL"].sum() / summary["Total_Cost"].sum() * 100
                             if summary["Total_Cost"].sum() else 0),
            "Margin":       summary["Margin"].sum(),
            "Net Cash":     summary["Net Cash"].sum(),
            "Dividends":    summary["Dividends"].sum(),
            "Rewards":      summary["Rewards"].sum(),
            "Margin Int":   summary["Margin Int"].sum(),
            "Fees":         summary["Fees"].sum(),
            "Net Income":   summary["Net Income"].sum(),
        }
        summary = pd.concat([summary, pd.DataFrame([_t])], ignore_index=True)

        disp_cols = ["Account", "Broker", "Positions",
                     "Market_Value", "Total_Cost", "PnL", "Return_%", "Margin",
                     "Net Cash", "Dividends", "Rewards", "Margin Int", "Fees", "Net Income"]
        money_cols_s = ["Market_Value", "Total_Cost", "PnL", "Margin",
                        "Net Cash", "Dividends", "Rewards", "Margin Int", "Fees", "Net Income"]
        fmt_s = {c: "${:,.2f}" for c in money_cols_s}
        fmt_s["Return_%"] = "{:+.2f}%"
        colour_cols_s = ["PnL", "Return_%", "Net Cash", "Dividends",
                         "Rewards", "Margin Int", "Fees", "Net Income"]
    else:
        # No positions file — show transaction-only summary
        _t2 = {
            "Account": "TOTAL", "Broker": "",
            "Net Cash": summary["Net Cash"].sum(),
            "Dividends": summary["Dividends"].sum(),
            "Rewards": summary["Rewards"].sum(),
            "Margin Int": summary["Margin Int"].sum(),
            "Fees": summary["Fees"].sum(),
            "Net Income": summary["Net Income"].sum(),
        }
        summary = pd.concat([summary, pd.DataFrame([_t2])], ignore_index=True)
        disp_cols = ["Account", "Broker", "Net Cash", "Dividends",
                     "Rewards", "Margin Int", "Fees", "Net Income"]
        money_cols_s = ["Net Cash", "Dividends", "Rewards", "Margin Int", "Fees", "Net Income"]
        fmt_s = {c: "${:,.2f}" for c in money_cols_s}
        colour_cols_s = money_cols_s

    st.subheader("Account Summary")
    st.dataframe(
        summary[disp_cols].style
            .format(fmt_s)
            .map(colour_cell, subset=colour_cols_s)
            .apply(_bold_last_row, last_idx=summary.index[-1], axis=1),
        use_container_width=True, hide_index=True,
    )

    st.divider()

    # ── 2. SECTOR ALLOCATION CHARTS ───────────────────────────────────────────
    if has_positions:
        total_mv = pos["MARKET VALUE"].sum()

        col_l, col_r = st.columns(2)
        with col_l:
            st.subheader("Sector Allocation")
            sec_grp = (
                pos.groupby("sector")["MARKET VALUE"].sum()
                   .sort_values(ascending=False).reset_index()
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
                   .sort_values(ascending=False).reset_index()
            )
            fig_acct_pie = px.pie(
                acct_grp, values="MARKET VALUE", names="Account",
                hole=0.4, color_discrete_sequence=ACCOUNT_COLOURS,
            )
            fig_acct_pie.update_traces(
                textposition="inside", textinfo="percent+label",
                hovertemplate="%{label}<br>$%{value:,.0f}<br>%{percent}",
            )
            fig_acct_pie.update_layout(showlegend=False, margin=dict(t=10, b=10))
            st.plotly_chart(fig_acct_pie, use_container_width=True)

        st.divider()

    # ── 3. POSITIONS BY ACCOUNT ────────────────────────────────────────────────
    if has_positions:
        st.subheader("Positions by Account")

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
        _pos_cols = ["Ticker", "Name", "TYPE", "sector", "Shares",
                     "PRICE", "Cost_Basis", "COST", "MARKET VALUE",
                     "totalReturn", "Return_%", "PERF_YTD", "IV_Rank", "ATR_pct"]

        acct_order = (
            summary[summary["Account"] != "TOTAL"]
            .sort_values("Market_Value", ascending=False)["Account"]
            .tolist()
        )

        for acct in acct_order:
            acct_pos = pos[pos["Account"] == acct].copy()
            if acct_pos.empty:
                continue
            acct_mv  = acct_pos["MARKET VALUE"].sum()
            acct_pnl = acct_pos["totalReturn"].sum()
            acct_ret = (acct_pnl / acct_pos["COST"].sum() * 100
                        if acct_pos["COST"].sum() else 0)
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
                _cost_safe = acct_pos["COST"].replace(0, float("nan"))
                acct_pos["Return_%"] = (
                    acct_pos["totalReturn"] / _cost_safe * 100
                ).fillna(0).round(2)
                show = [c for c in _pos_cols if c in acct_pos.columns]
                fmt  = {k: v for k, v in _pos_fmt.items() if k in show}
                st.dataframe(
                    acct_pos[show]
                        .sort_values("MARKET VALUE", ascending=False)
                        .reset_index(drop=True)
                        .style.format(fmt)
                        .map(colour_cell, subset=["totalReturn", "Return_%"]),
                    use_container_width=True,
                    hide_index=True,
                )

        st.divider()

    # ── 4. SECTOR SUMMARY TABLE ────────────────────────────────────────────────
    if has_positions:
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

        # Join lifetime dividends aggregated to sector level
        _sec_divs = (
            df_all[df_all["category"] == "dividend"]
            .merge(pos[["Ticker", "sector"]].drop_duplicates("Ticker"),
                   left_on="symbol", right_on="Ticker", how="inner")
            .groupby("sector")["amount"].sum()
            .reset_index()
            .rename(columns={"amount": "Dividends"})
        )
        sec_tbl = sec_tbl.merge(_sec_divs, on="sector", how="left")
        sec_tbl["Dividends"] = sec_tbl["Dividends"].fillna(0)

        st.subheader("Sector Summary")
        st.dataframe(
            sec_tbl.style
                .format({"Market_Value": "${:,.2f}", "Total_Cost": "${:,.2f}",
                         "PnL": "${:+,.2f}", "Alloc_%": "{:.2f}%",
                         "Return_%": "{:+.2f}%", "Dividends": "${:,.2f}"})
                .map(colour_cell, subset=["PnL", "Return_%", "Dividends"]),
            use_container_width=True, hide_index=True,
        )
        st.divider()


# ═══ TAB 2 — Yearly Summary ═══════════════════════════════════════════════════
with tab_yearly:
    df["year"] = df["date"].dt.year
    years = sorted(df["year"].dropna().unique().astype(int))

    # ── Year-over-year table ───────────────────────────────────────────────────
    st.subheader("Year-over-Year Summary")
    yr_rows = []
    for yr in years:
        yd  = df[df["year"] == yr]
        ym  = compute_metrics(yd)
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
            "Net Income":      _net_income_fn(ym),
        })

    # Totals row
    t = compute_metrics(df)
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
        "Net Income":      _net_income_fn(t),
    })

    yr_df = pd.DataFrame(yr_rows)
    yr_money = ["Deposits", "Withdrawals", "Net Cash", "Dividends", "Rewards",
                "Div + Rewards", "Margin Interest", "Fees", "Net Income"]
    st.dataframe(style_table(yr_df, yr_money), use_container_width=True, hide_index=True)

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


# ═══ TAB 3 — By Account ═══════════════════════════════════════════════════════
with tab_breakdown:
    import datetime as _dt

    _curr_yr = _dt.date.today().year
    _prev_yr = _curr_yr - 1

    df_acct = df.copy()
    df_acct["year"] = df_acct["date"].dt.year
    _available = set(df_acct["year"].dropna().unique().astype(int))
    # Show only prev-year and current-year columns, whichever exist in the data
    _pivot_years = [y for y in [_prev_yr, _curr_yr] if y in _available]

    def _pivot(source: pd.DataFrame, metric_fn) -> pd.DataFrame:
        """Build Account × (Prev Year | Curr Year | ALL) pivot with a TOTAL row."""
        rows = {}
        for acct in [a for a in all_accounts if a in accounts]:
            ad = source[source["account_id"] == acct]
            rows[acct] = {yr: metric_fn(compute_metrics(ad[ad["year"] == yr]))
                          for yr in _pivot_years}
            rows[acct]["ALL"] = metric_fn(compute_metrics(ad))
        pv = pd.DataFrame(rows).T.reset_index().rename(columns={"index": "Account"})
        totals = {"Account": "TOTAL"}
        for yr in _pivot_years:
            totals[yr] = metric_fn(compute_metrics(source[source["year"] == yr]))
        totals["ALL"] = metric_fn(compute_metrics(source))
        pv = pd.concat([pv, pd.DataFrame([totals])], ignore_index=True)
        return pv[["Account"] + _pivot_years + ["ALL"]]

    def _show_pivot(pv: pd.DataFrame, title: str):
        yr_cols = [c for c in pv.columns if c != "Account"]
        st.subheader(title)
        st.dataframe(style_table(pv, yr_cols), use_container_width=True, hide_index=True)

    _show_pivot(_pivot(df_acct, lambda m: m["net_cash"]),                "Net Cash Flow by Account")
    st.divider()
    _show_pivot(_pivot(df_acct, lambda m: m["dividends"] + m["rewards"]), "Div + Rewards by Account")
    st.divider()
    _show_pivot(_pivot(df_acct, lambda m: m["margin_int"] + m["fees"]),   "Margin + Fees by Account")

    # ── Crypto Flow ────────────────────────────────────────────────────────────
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
            "usd_deposit":     "USD Deposited (direct)",
            "bank_purchase":   "Bought Crypto via Bank / PayPal",
            "crypto_received": "Crypto Received (external wallet)",
            "usd_withdrawal":  "USD Withdrawn",
            "crypto_sent":     "Crypto Sent (external wallet)",
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
            st.dataframe(in_df.style.format({"Amount": "${:,.2f}"})
                             .apply(_bold_last_row, last_idx=in_df.index[-1], axis=1),
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
            st.dataframe(out_df.style.format({"Amount": "${:,.2f}"})
                             .apply(_bold_last_row, last_idx=out_df.index[-1], axis=1),
                         use_container_width=True, hide_index=True)
        with col_net:
            st.markdown("**Net**")
            st.metric("Total In",  f"${total_in:,.2f}")
            st.metric("Total Out", f"${total_out:,.2f}")
            st.metric("Net Cash",  f"${net:,.2f}", delta=f"${net:,.2f}")


# ═══ TAB 4 — Positions ════════════════════════════════════════════════════════
with tab_positions:
    pos_raw = _load_positions()
    if pos_raw.empty:
        st.info("No positions file found — add activity/TRADEPOSITIONS.xlsx to enable this view.")
    else:
        # Exclude margin rows; numeric coerce
        _pos = pos_raw[pos_raw["Ticker"] != "MARGIN"].copy()
        for _c in ["COST", "MARKET VALUE", "totalReturn"]:
            _pos[_c] = pd.to_numeric(_pos[_c], errors="coerce")

        # Aggregate by symbol (sum across accounts)
        sym = (
            _pos.groupby(["Ticker", "Name", "sector"])
                .agg(
                    Market_Value=("MARKET VALUE", "sum"),
                    Total_Cost   =("COST",         "sum"),
                    PnL          =("totalReturn",  "sum"),
                )
                .reset_index()
                .sort_values("Market_Value", ascending=False)
        )
        sym["Return_%"] = (
            sym["PnL"] / sym["Total_Cost"].replace(0, float("nan")) * 100
        ).round(2)

        # Lifetime dividends per symbol from the full DB (not date-filtered)
        _divs = (
            df_all[df_all["category"] == "dividend"]
            .groupby("symbol")["amount"]
            .sum()
            .reset_index()
            .rename(columns={"symbol": "Ticker", "amount": "Dividends"})
        )
        sym = sym.merge(_divs, on="Ticker", how="left")
        sym["Dividends"] = sym["Dividends"].fillna(0)

        # Pre-compute totals before building the sortable table
        _t_mv   = sym["Market_Value"].sum()
        _t_cost = sym["Total_Cost"].sum()
        _t_pnl  = sym["PnL"].sum()
        _t_ret  = (_t_pnl / _t_cost * 100 if _t_cost else 0)
        _t_div  = sym["Dividends"].sum()

        _pos_cols = ["Ticker", "Name", "sector", "Market_Value", "Total_Cost",
                     "PnL", "Return_%", "Dividends"]
        _money_p  = ["Market_Value", "Total_Cost", "PnL", "Dividends"]
        _colour_p = ["PnL", "Return_%"]
        _fmt_p    = {c: "${:,.2f}" for c in _money_p}
        _fmt_p["Return_%"] = "{:+.2f}%"

        st.subheader(f"Positions by Symbol — {len(sym)} holdings")
        st.dataframe(
            sym[_pos_cols].style
                .format(_fmt_p)
                .map(colour_cell, subset=_colour_p),
            use_container_width=True,
            hide_index=True,
        )

        # Fixed footer — metrics never participate in table sorting
        st.markdown(
            "<hr style='margin:4px 0; border-color:#6b7280'>",
            unsafe_allow_html=True,
        )
        fc1, fc2, fc3, fc4, fc5 = st.columns(5)
        fc1.metric("Market Value", f"${_t_mv:,.0f}")
        fc2.metric("Total Cost",   f"${_t_cost:,.0f}")
        fc3.metric("P&L",          f"${_t_pnl:+,.0f}")
        fc4.metric("Return",       f"{_t_ret:+.2f}%",
                   delta=f"{_t_ret:+.2f}%", delta_color="normal")
        fc5.metric("Dividends",    f"${_t_div:,.0f}")


# ═══ TAB 5 — Transactions ══════════════════════════════════════════════════════
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

