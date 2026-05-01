# -*- coding: utf-8 -*-
"""Portfolio Journal — Streamlit dashboard.
Run: streamlit run dashboard/app.py
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from src.db import DB_PATH, init_db, load_transactions, load_snapshot_periods, get_cash_balance

# Ensure schema is up-to-date (creates new tables/views if this is an existing DB).
if DB_PATH.exists():
    init_db()

# ── Background ingest on startup ───────────────────────────────────────────────
# Runs once per browser session; never blocks the UI.
_ROOT = Path(__file__).parent.parent
if "ingest_proc" not in st.session_state:
    st.session_state.ingest_proc = subprocess.Popen(
        [sys.executable, str(_ROOT / "ingest.py")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(_ROOT),
    )
    st.session_state.ingest_done = False

# If ingest just finished, flush cached data and re-render with fresh DB.
_proc: subprocess.Popen = st.session_state.ingest_proc
if not st.session_state.ingest_done and _proc.poll() is not None:
    st.session_state.ingest_done = True
    st.cache_data.clear()
    st.rerun()
from src.metrics import compute_metrics, net_income as _net_income_fn, colour_cell, style_table, _bold_last_row
from src.positions import (
    load_positions_from_db, compute_net_worth, load_all_positions,
    load_options_from_db, load_futures_from_db, load_crypto_from_db,
)

@st.cache_data(ttl=300)
def _load_positions() -> pd.DataFrame:
    """Load equity positions from DB with live prices via yfinance (cached 5 min)."""
    return load_positions_from_db()


@st.cache_data(ttl=300)
def _load_all_positions() -> pd.DataFrame:
    """Load all position types (equity + options + futures + crypto, cached 5 min)."""
    return load_all_positions()


@st.cache_data(ttl=300)
def _load_options() -> pd.DataFrame:
    return load_options_from_db()


@st.cache_data(ttl=300)
def _load_futures() -> pd.DataFrame:
    return load_futures_from_db()


@st.cache_data(ttl=300)
def _load_crypto() -> pd.DataFrame:
    return load_crypto_from_db()


@st.cache_data(ttl=300)
def _load_snapshot_periods() -> pd.DataFrame:
    """Load per-account snapshot periods from DB (cached 5 min)."""
    return load_snapshot_periods()


@st.cache_data(ttl=300)
def _load_cash_balance() -> float:
    """Load combined cash account balance from DB (cached 5 min)."""
    return get_cash_balance()

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
    if st.session_state.get("ingest_done", False):
        st.success("Data refreshed", icon="✅")
    else:
        st.info("Refreshing data…", icon="🔄")
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
_cash_balance = _load_cash_balance()
try:
    _nw_data = compute_net_worth(_load_all_positions())
    if _nw_data["market_value"]:
        _total_mv     = _nw_data["market_value"] + _cash_balance
        _total_margin = _nw_data["margin"]
        _net_worth    = _nw_data["net_worth"] + _cash_balance
        nw1, nw2, nw3 = st.columns(3)
        nw1.metric("Net Worth",       f"${_net_worth:,.0f}")
        nw2.metric("Market Value",    f"${_total_mv:,.0f}")
        nw3.metric("Margin Borrowed", f"${_total_margin:,.0f}",
                   delta=f"-${_total_margin:,.0f}", delta_color="inverse")
except Exception:
    pass  # Net worth banner is best-effort; failures must not crash the page

# ── Summary table (replaces individual metric widgets) ─────────────────────────
_kpi_row = {
    "Cash In/Out":  m_all["net_cash"],
    "Div+Rewards":    m_all["dividends"] + m_all["rewards"],
    "Costs":          m_all["margin_int"] + m_all["fees"],
    "Net Income":     _net_income_fn(m_all),
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
tab_portfolio, tab_yearly, tab_breakdown, tab_positions, tab_txns, tab_perf = st.tabs([
    "Portfolio", "Yearly Summary", "By Account", "Positions", "Transactions", "Performance"
])


# ═══ TAB 1 — Portfolio ════════════════════════════════════════════════════════
with tab_portfolio:

    # ── Load positions data ────────────────────────────────────────────────────
    pos_all  = _load_positions()
    opts_all = _load_options()
    futs_all = _load_futures()
    has_positions = not pos_all.empty
    has_opts = not opts_all.empty
    has_futs = not futs_all.empty

    if has_positions:
        _is_margin = pos_all["Ticker"].str.upper() == "MARGIN"
        margin_df  = pos_all[_is_margin].copy()
        pos        = pos_all[~_is_margin].copy()
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
        # Non-equity MV per account (options + futures + crypto)
        _non_eq_frames = []
        for _ldr in (_load_options, _load_futures, _load_crypto):
            _nef = _ldr()
            if not _nef.empty and "MARKET VALUE" in _nef.columns:
                _nef = _nef[["Account", "MARKET VALUE"]].copy()
                _nef["MARKET VALUE"] = pd.to_numeric(_nef["MARKET VALUE"], errors="coerce")
                _non_eq_frames.append(_nef)
        if _non_eq_frames:
            _non_eq_mv = (
                pd.concat(_non_eq_frames, ignore_index=True)
                  .groupby("Account")["MARKET VALUE"].sum()
                  .reset_index()
                  .rename(columns={"MARKET VALUE": "Other_MV"})
            )
        else:
            _non_eq_mv = pd.DataFrame(columns=["Account", "Other_MV"])

    # ── 1. UNIFIED ACCOUNT SUMMARY ─────────────────────────────────────────────
    tx_rows = []
    for acct in [a for a in all_accounts if a in accounts]:
        ad = df[df["account_id"] == acct]
        if ad.empty:
            continue
        am = compute_metrics(ad)
        tx_rows.append({
            "Account":        acct,
            "Broker":         ad["broker"].iloc[0],
            "Cash In/Out":  am["net_cash"],
            "Div+Rewards":    am["dividends"] + am["rewards"],
            "Costs":          am["margin_int"] + am["fees"],
            "Net Income":     _net_income_fn(am),
        })
    summary = pd.DataFrame(tx_rows).fillna(0)

    if has_positions:
        summary = (
            summary
            .merge(pos_by_acct,    on="Account", how="left")
            .merge(margin_by_acct, on="Account", how="left")
            .merge(_non_eq_mv,     on="Account", how="left")
        )
        summary["Positions"]    = summary["Positions"].fillna(0).astype(int)
        summary["Equity"]       = summary["Market_Value"].fillna(0)
        summary["Total_Cost"]   = summary["Total_Cost"].fillna(0)
        summary["PnL"]          = summary["PnL"].fillna(0)
        summary["Margin"]       = summary["Margin"].fillna(0).abs()
        summary["Other_MV"]     = summary["Other_MV"].fillna(0)
        summary["Market Value"] = summary["Equity"] + summary["Other_MV"] - summary["Margin"]
        summary["Return_%"]     = (
            summary["PnL"] / summary["Total_Cost"].replace(0, float("nan")) * 100
        ).fillna(0).round(2)

        # Cash & Savings row (if balance is set)
        if _cash_balance > 0:
            _cash_row = {
                "Account":      "CASH",
                "Broker":       "Multi-Bank",
                "Positions":    0,
                "Equity":       0.0,
                "Total_Cost":   0.0,
                "PnL":          0.0,
                "Return_%":     0.0,
                "Margin":       0.0,
                "Other_MV":     0.0,
                "Market Value": _cash_balance,
                "Cash In/Out":  0.0,
                "Div+Rewards":  0.0,
                "Costs":        0.0,
                "Net Income":   0.0,
            }
            summary = pd.concat([summary, pd.DataFrame([_cash_row])], ignore_index=True)

        # Totals row
        _t = {
            "Account":        "TOTAL",
            "Broker":         "",
            "Positions":      int(summary["Positions"].sum()),
            "Equity":         summary["Equity"].sum(),
            "Total_Cost":     summary["Total_Cost"].sum(),
            "PnL":            summary["PnL"].sum(),
            "Return_%":       (summary["PnL"].sum() / summary["Total_Cost"].sum() * 100
                               if summary["Total_Cost"].sum() else 0),
            "Margin":         summary["Margin"].sum(),
            "Market Value":   summary["Market Value"].sum(),
            "Cash In/Out":  summary["Cash In/Out"].sum(),
            "Div+Rewards":    summary["Div+Rewards"].sum(),
            "Costs":          summary["Costs"].sum(),
            "Net Income":     summary["Net Income"].sum(),
        }
        summary = pd.concat([summary, pd.DataFrame([_t])], ignore_index=True)

        disp_cols = ["Account", "Broker",
                     "Equity", "Margin", "Market Value",
                     "Total_Cost", "PnL", "Return_%",
                     "Cash In/Out", "Div+Rewards", "Costs", "Net Income"]
        money_cols_s = ["Equity", "Margin", "Market Value",
                        "Total_Cost", "PnL",
                        "Cash In/Out", "Div+Rewards", "Costs", "Net Income"]
        fmt_s = {c: "${:,.0f}" for c in money_cols_s}
        fmt_s["Return_%"] = "{:+.1f}%"
        colour_cols_s = ["PnL", "Return_%", "Market Value",
                         "Cash In/Out", "Div+Rewards", "Costs", "Net Income"]
    else:
        # No positions file — show transaction-only summary
        _t2 = {
            "Account":       "TOTAL", "Broker": "",
            "Cash In/Out": summary["Cash In/Out"].sum(),
            "Div+Rewards":   summary["Div+Rewards"].sum(),
            "Costs":         summary["Costs"].sum(),
            "Net Income":    summary["Net Income"].sum(),
        }
        summary = pd.concat([summary, pd.DataFrame([_t2])], ignore_index=True)
        disp_cols = ["Account", "Broker", "Cash In/Out", "Div+Rewards", "Costs", "Net Income"]
        money_cols_s = ["Cash In/Out", "Div+Rewards", "Costs", "Net Income"]
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

    # ── 2. SECTOR ALLOCATION CHART ────────────────────────────────────────────
    if has_positions:
        total_mv = pos["MARKET VALUE"].sum()

        st.subheader("Sector Allocation")
        # Collapse ETFs: keep "Income ETF" as-is; everything else with TYPE=="ETF"
        # becomes "ETF" so the chart shows exactly two ETF buckets.
        _sec_display = pos["sector"].copy()
        if "TYPE" in pos.columns:
            _is_etf = pos["TYPE"].str.upper().eq("ETF") & (_sec_display != "Income ETF")
            _sec_display = _sec_display.where(~_is_etf, "ETF")
        sec_grp = (
            pd.Series(_sec_display.values, name="sector")
            .to_frame()
            .assign(mv=pos["MARKET VALUE"].values)
            .groupby("sector")["mv"].sum()
            .sort_values(ascending=False)
            .reset_index()
            .rename(columns={"mv": "MARKET VALUE"})
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
            .sort_values("Equity", ascending=False)["Account"]
            .tolist()
        )

        for acct in acct_order:
            acct_pos  = pos[pos["Account"] == acct].copy()
            if acct_pos.empty:
                continue
            acct_mv     = acct_pos["MARKET VALUE"].sum()
            acct_pnl    = acct_pos["totalReturn"].sum()
            acct_ret    = (acct_pnl / acct_pos["COST"].sum() * 100
                           if acct_pos["COST"].sum() else 0)
            acct_margin = abs(float(
                margin_df[margin_df["Account"] == acct]["MARKET VALUE"].sum()
            ))
            acct_opts   = opts_all[opts_all["Account"] == acct] if has_opts else pd.DataFrame()
            label = (
                f"**{acct}** — {len(acct_pos)} positions · "
                f"MV ${acct_mv:,.0f} · "
                f"P&L ${acct_pnl:+,.0f} ({acct_ret:+.1f}%) · "
                f"Margin ${acct_margin:,.0f}"
                + (f" · Options ${acct_opts['MARKET VALUE'].sum():,.0f}" if not acct_opts.empty else "")
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
                if not acct_opts.empty:
                    st.markdown("**Options**")
                    opt_show = [c for c in ["symbol", "underlying", "expiry", "strike",
                                            "call_put", "qty", "price", "MARKET VALUE"]
                                if c in acct_opts.columns]
                    st.dataframe(
                        acct_opts[opt_show].reset_index(drop=True)
                            .style.format({"MARKET VALUE": "${:,.2f}", "price": "${:.2f}",
                                           "strike": "${:.2f}"}),
                        use_container_width=True, hide_index=True,
                    )

        st.divider()

    # ── 4. SECTOR SUMMARY TABLE ────────────────────────────────────────────────
    if has_positions:
        # Use the same collapsed sector labels as the pie chart
        _sec_tbl_src = pos.copy()
        _sec_tbl_src["sector"] = _sec_display.values
        sec_tbl = (
            _sec_tbl_src.groupby("sector")
               .agg(Market_Value=("MARKET VALUE", "sum"),
                    Total_Cost  =("COST",         "sum"),
                    PnL         =("totalReturn",  "sum"))
               .reset_index()
               .sort_values("Market_Value", ascending=False)
        )
        sec_tbl["Alloc_%"]  = (sec_tbl["Market_Value"] / total_mv * 100).round(2)
        sec_tbl["Return_%"] = (sec_tbl["PnL"] / sec_tbl["Total_Cost"] * 100).round(2)

        # Join lifetime dividends aggregated to collapsed sector level
        _pos_with_collapsed = pos[["Ticker", "sector"]].copy()
        _pos_with_collapsed["sector"] = _sec_display.values
        _sec_divs = (
            df_all[df_all["category"] == "dividend"]
            .merge(_pos_with_collapsed.drop_duplicates("Ticker"),
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
                .format({"Market_Value": "${:,.0f}", "Total_Cost": "${:,.0f}",
                         "PnL": "${:+,.0f}", "Alloc_%": "{:.2f}%",
                         "Return_%": "{:+.2f}%", "Dividends": "${:,.0f}"})
                .map(colour_cell, subset=["PnL", "Return_%", "Dividends"]),
            use_container_width=True, hide_index=True,
        )
        st.divider()

    # ── Options Summary ───────────────────────────────────────────────────────
    if has_opts:
        import datetime as _odt
        st.subheader("Options Summary")
        _opt_mv_total = opts_all["MARKET VALUE"].sum()
        _today = _odt.date.today()
        oc1, oc2, oc3 = st.columns(3)
        oc1.metric("Open Positions", len(opts_all))
        oc2.metric("Total Market Value", f"${_opt_mv_total:,.0f}")
        if "expiry" in opts_all.columns:
            _expiring = opts_all[pd.to_datetime(opts_all["expiry"], errors="coerce").dt.date
                                 <= (_today + _odt.timedelta(days=7))]
            oc3.metric("Expiring This Week", len(_expiring))
        opt_disp_cols = [c for c in ["Account", "symbol", "underlying", "expiry",
                                      "strike", "call_put", "qty", "price", "MARKET VALUE"]
                         if c in opts_all.columns]
        st.dataframe(
            opts_all[opt_disp_cols]
                .sort_values("expiry" if "expiry" in opts_all.columns else opt_disp_cols[0])
                .reset_index(drop=True)
                .style.format({"MARKET VALUE": "${:,.2f}", "price": "${:.2f}", "strike": "${:.2f}"}),
            use_container_width=True, hide_index=True,
        )
        st.divider()

    # ── Futures Summary ───────────────────────────────────────────────────────
    if has_futs:
        st.subheader("Futures Summary")
        _fut_mv_total = futs_all["MARKET VALUE"].sum()
        fc1, fc2 = st.columns(2)
        fc1.metric("Open Contracts", len(futs_all))
        fc2.metric("Net Market Value", f"${_fut_mv_total:+,.0f}")
        fut_disp_cols = [c for c in ["Account", "symbol", "qty", "price", "MARKET VALUE"]
                         if c in futs_all.columns]
        st.dataframe(
            futs_all[fut_disp_cols].reset_index(drop=True)
                .style.format({"MARKET VALUE": "${:+,.2f}", "price": "${:,.4f}"})
                .map(colour_cell, subset=["MARKET VALUE"]),
            use_container_width=True, hide_index=True,
        )


# ═══ TAB 2 — Yearly Summary ═══════════════════════════════════════════════════
with tab_yearly:
    import datetime as _yrdt

    df["year"] = df["date"].dt.year
    _yr_curr  = _yrdt.date.today().year
    _yr_prev  = _yr_curr - 1
    _yr_avail = set(df["year"].dropna().unique().astype(int))
    _yr_cols  = [y for y in [_yr_prev, _yr_curr] if y in _yr_avail]

    # ── Transposed summary: rows = metrics, cols = [YR-1, YR, ALL] ────────────
    _metric_defs = [
        ("Deposits",        lambda m: m["deposits"]),
        ("Withdrawals",     lambda m: m["withdrawals"]),
        ("Net Cash",        lambda m: m["net_cash"]),
        ("Dividends",       lambda m: m["dividends"]),
        ("Rewards",         lambda m: m["rewards"]),
        ("Div + Rewards",   lambda m: m["dividends"] + m["rewards"]),
        ("Margin Interest", lambda m: m["margin_int"]),
        ("Fees",            lambda m: m["fees"]),
        ("Net Income",      lambda m: _net_income_fn(m)),
    ]

    _yr_summary_rows = []
    for _label, _fn in _metric_defs:
        _row = {"Metric": _label}
        for _yr in _yr_cols:
            _row[_yr] = _fn(compute_metrics(df[df["year"] == _yr]))
        _row["ALL"] = _fn(compute_metrics(df))
        _yr_summary_rows.append(_row)

    yr_df = pd.DataFrame(_yr_summary_rows)
    _yr_val_cols = [c for c in yr_df.columns if c != "Metric"]
    fmt_yr = {c: "${:,.0f}" for c in _yr_val_cols}

    st.subheader("Year-over-Year Summary")
    st.dataframe(
        yr_df.style
            .format(fmt_yr)
            .map(colour_cell, subset=_yr_val_cols)
            .apply(_bold_last_row, last_idx=yr_df.index[-1], axis=1),
        use_container_width=True, hide_index=True,
    )

    st.divider()

    # ── Drilldown — Income by Type, transposed: rows = type, cols = [YR-1, YR, ALL] ──
    st.subheader("Income Breakdown by Type")
    _inc_src = df[df["category"].isin(["dividend", "reward"]) & (df["amount"] > 0)]
    if not _inc_src.empty:
        _inc_pivot = (
            _inc_src.groupby(["subcategory", "year"])["amount"].sum()
            .unstack(fill_value=0)
        )
        _inc_yr_cols = [y for y in _yr_cols if y in _inc_pivot.columns]
        _inc_pivot["ALL"] = _inc_pivot.sum(axis=1)
        _inc_tbl = (
            _inc_pivot[_inc_yr_cols + ["ALL"]]
            .reset_index()
            .rename(columns={"subcategory": "Type"})
            .sort_values("ALL", ascending=False)
            .reset_index(drop=True)
        )
        _inc_val_cols = [c for c in _inc_tbl.columns if c != "Type"]
        st.dataframe(
            _inc_tbl.style
                .format({c: "${:,.0f}" for c in _inc_val_cols})
                .map(colour_cell, subset=_inc_val_cols),
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
    _all_brokers = sorted(df_all["broker"].dropna().unique()) if "broker" in df_all.columns else []
    _sel_brokers = st.multiselect("Broker filter", _all_brokers, default=_all_brokers,
                                   key="pos_broker_filter")
    _acct_broker = (df_all[["account_id", "broker"]].drop_duplicates()
                    .set_index("account_id")["broker"])

    def _filter_pos(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty or "Account" not in frame.columns:
            return frame
        return frame[frame["Account"].map(_acct_broker).isin(_sel_brokers)].copy()

    _ptab_eq, _ptab_opt, _ptab_fut, _ptab_cry = st.tabs(
        ["Equity", "Options", "Futures", "Crypto"]
    )

    # ── Equity sub-tab ─────────────────────────────────────────────────────────
    with _ptab_eq:
        pos_raw = _filter_pos(_load_positions())
        if pos_raw.empty:
            st.info("No equity positions — run ingest after adding positions-{account}.csv files to activity/.")
        else:
            _pos = pos_raw[pos_raw["Ticker"].str.upper() != "MARGIN"].copy()
            for _c in ["COST", "MARKET VALUE", "totalReturn"]:
                _pos[_c] = pd.to_numeric(_pos[_c], errors="coerce")

            sym = (
                _pos.groupby(["Ticker", "Name", "sector"])
                    .agg(
                        Market_Value=("MARKET VALUE", "sum"),
                        Total_Cost  =("COST",         "sum"),
                        PnL         =("totalReturn",  "sum"),
                    )
                    .reset_index()
                    .sort_values("Market_Value", ascending=False)
            )
            sym["Return_%"] = (
                sym["PnL"] / sym["Total_Cost"].replace(0, float("nan")) * 100
            ).round(2)

            _divs = (
                df_all[df_all["category"] == "dividend"]
                .groupby("symbol")["amount"]
                .sum()
                .reset_index()
                .rename(columns={"symbol": "Ticker", "amount": "Dividends"})
            )
            sym = sym.merge(_divs, on="Ticker", how="left")
            sym["Dividends"] = sym["Dividends"].fillna(0)

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

            st.subheader(f"Equity — {len(sym)} holdings")
            st.dataframe(
                sym[_pos_cols].style
                    .format(_fmt_p)
                    .map(colour_cell, subset=_colour_p),
                use_container_width=True,
                hide_index=True,
            )
            st.markdown("<hr style='margin:4px 0; border-color:#6b7280'>", unsafe_allow_html=True)
            fc1, fc2, fc3, fc4, fc5 = st.columns(5)
            fc1.metric("Market Value", f"${_t_mv:,.0f}")
            fc2.metric("Total Cost",   f"${_t_cost:,.0f}")
            fc3.metric("P&L",          f"${_t_pnl:+,.0f}")
            fc4.metric("Return",       f"{_t_ret:+.2f}%",
                       delta=f"{_t_ret:+.2f}%", delta_color="normal")
            fc5.metric("Dividends",    f"${_t_div:,.0f}")

    # ── Options sub-tab ────────────────────────────────────────────────────────
    with _ptab_opt:
        opt_df = _filter_pos(_load_options())
        if opt_df.empty:
            st.info("No options positions in the database.")
        else:
            for _c in ["qty", "price", "MARKET VALUE", "strike"]:
                if _c in opt_df.columns:
                    opt_df[_c] = pd.to_numeric(opt_df[_c], errors="coerce")

            _opt_total_mv = float(opt_df["MARKET VALUE"].fillna(0).sum())

            # Per-account breakdown
            for _acct, _grp in opt_df.groupby("Account"):
                _acct_mv = float(_grp["MARKET VALUE"].fillna(0).sum())
                _label   = f"**{_acct}** — {len(_grp)} contracts · MV ${_acct_mv:,.0f}"
                with st.expander(_label, expanded=True):
                    _show_cols = [c for c in
                                  ["symbol", "underlying", "expiry", "strike", "call_put",
                                   "qty", "price", "MARKET VALUE", "description"]
                                  if c in _grp.columns]
                    _opt_fmt = {}
                    for _fc in ["price", "strike"]:
                        if _fc in _show_cols:
                            _opt_fmt[_fc] = "${:.2f}"
                    if "MARKET VALUE" in _show_cols:
                        _opt_fmt["MARKET VALUE"] = "${:,.2f}"
                    st.dataframe(
                        _grp[_show_cols]
                            .sort_values("MARKET VALUE", ascending=False)
                            .reset_index(drop=True)
                            .style.format(_opt_fmt),
                        use_container_width=True, hide_index=True,
                    )

            st.markdown("<hr style='margin:4px 0; border-color:#6b7280'>", unsafe_allow_html=True)
            _oc1, _oc2 = st.columns(2)
            _oc1.metric("Total Contracts", len(opt_df))
            _oc2.metric("Total Market Value", f"${_opt_total_mv:,.0f}")

    # ── Futures sub-tab ────────────────────────────────────────────────────────
    with _ptab_fut:
        fut_df = _filter_pos(_load_futures())
        if fut_df.empty:
            st.info("No futures positions in the database.")
        else:
            for _c in ["qty", "price", "MARKET VALUE"]:
                if _c in fut_df.columns:
                    fut_df[_c] = pd.to_numeric(fut_df[_c], errors="coerce")

            _fut_total_mv = float(fut_df["MARKET VALUE"].fillna(0).sum())

            for _acct, _grp in fut_df.groupby("Account"):
                _acct_mv = float(_grp["MARKET VALUE"].fillna(0).sum())
                _label   = f"**{_acct}** — {len(_grp)} contracts · Net MV ${_acct_mv:+,.0f}"
                with st.expander(_label, expanded=True):
                    _show_cols = [c for c in
                                  ["symbol", "underlying", "description", "qty",
                                   "price", "MARKET VALUE"]
                                  if c in _grp.columns]
                    _fut_fmt = {}
                    if "price" in _show_cols:
                        _fut_fmt["price"] = "${:,.2f}"
                    if "MARKET VALUE" in _show_cols:
                        _fut_fmt["MARKET VALUE"] = "${:+,.2f}"
                    st.dataframe(
                        _grp[_show_cols]
                            .sort_values("MARKET VALUE", ascending=False)
                            .reset_index(drop=True)
                            .style.format(_fut_fmt)
                            .map(colour_cell, subset=["MARKET VALUE"]),
                        use_container_width=True, hide_index=True,
                    )

            st.markdown("<hr style='margin:4px 0; border-color:#6b7280'>", unsafe_allow_html=True)
            _fc1, _fc2 = st.columns(2)
            _fc1.metric("Total Contracts", len(fut_df))
            _fc2.metric("Net Market Value", f"${_fut_total_mv:+,.0f}")

    # ── Crypto sub-tab ─────────────────────────────────────────────────────────
    with _ptab_cry:
        cry_df = _filter_pos(_load_crypto())
        if cry_df.empty:
            st.info("No crypto positions in the database.")
        else:
            for _c in ["qty", "price", "cost_basis", "MARKET VALUE"]:
                if _c in cry_df.columns:
                    cry_df[_c] = pd.to_numeric(cry_df[_c], errors="coerce")

            _cry_total_mv   = float(cry_df["MARKET VALUE"].fillna(0).sum())
            _cry_total_cost = float(cry_df["cost_basis"].fillna(0).sum()) if "cost_basis" in cry_df.columns else 0.0
            _cry_pnl        = _cry_total_mv - _cry_total_cost

            _show_cols = [c for c in
                          ["Ticker", "name", "qty", "price", "cost_basis", "MARKET VALUE"]
                          if c in cry_df.columns]
            _cry_fmt = {c: "${:,.4f}" for c in ["price"]} if "price" in _show_cols else {}
            for _mc in ["cost_basis", "MARKET VALUE"]:
                if _mc in _show_cols:
                    _cry_fmt[_mc] = "${:,.2f}"

            st.subheader(f"Crypto — {len(cry_df)} holdings")
            st.dataframe(
                cry_df[_show_cols]
                    .sort_values("MARKET VALUE", ascending=False)
                    .reset_index(drop=True)
                    .style.format(_cry_fmt),
                use_container_width=True, hide_index=True,
            )
            st.markdown("<hr style='margin:4px 0; border-color:#6b7280'>", unsafe_allow_html=True)
            _cc1, _cc2, _cc3 = st.columns(3)
            _cc1.metric("Market Value", f"${_cry_total_mv:,.0f}")
            _cc2.metric("Cost Basis",   f"${_cry_total_cost:,.0f}")
            _cc3.metric("P&L",          f"${_cry_pnl:+,.0f}")


# ═══ TAB 5 — Transactions ══════════════════════════════════════════════════════
with tab_txns:
    col1, col2, col3, col4 = st.columns([2, 2, 2, 3])
    with col1:
        cat_filter = st.multiselect("Category",
                                    sorted(df["category"].unique()),
                                    default=sorted(df["category"].unique()))
    with col2:
        _txn_brokers = sorted(df["broker"].dropna().unique()) if "broker" in df.columns else []
        broker_filter = st.multiselect("Broker", _txn_brokers, default=_txn_brokers)
    with col3:
        yr_filter = st.multiselect("Year",
                                   sorted(df["date"].dt.year.unique().astype(int), reverse=True),
                                   default=[])
    with col4:
        search = st.text_input("Search description",
                               placeholder="AAPL, margin, staking …")

    txn = df[df["category"].isin(cat_filter)].copy()
    if "broker" in txn.columns:
        txn = txn[txn["broker"].isin(broker_filter)]
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


# ═══ TAB 6 — Performance ══════════════════════════════════════════════════════
with tab_perf:
    st.subheader("Portfolio Performance")

    try:
        _all_pos  = _load_all_positions()
        _snap_df  = _load_snapshot_periods()
    except Exception as _exc:
        st.error(f"Failed to load performance data: {_exc}")
        _all_pos  = pd.DataFrame()
        _snap_df  = pd.DataFrame()

    if _all_pos.empty:
        st.info("No positions loaded yet. Run `python ingest.py` to populate positions data.")
    else:
        # ── Compute live current value per account ─────────────────────────────
        _is_margin_p = _all_pos["Ticker"].str.upper() == "MARGIN"

        _live_mv = (
            _all_pos[~_is_margin_p]
            .groupby("Account")["MARKET VALUE"]
            .sum()
            .reset_index()
            .rename(columns={"Account": "account_id", "MARKET VALUE": "current_value"})
        )
        _live_mv["current_value"] = pd.to_numeric(_live_mv["current_value"], errors="coerce")

        _margin_mv = (
            _all_pos[_is_margin_p]
            .groupby("Account")["MARKET VALUE"]
            .sum()
            .abs()
            .reset_index()
            .rename(columns={"Account": "account_id", "MARKET VALUE": "margin"})
        )

        # ── Merge with snapshot periods ────────────────────────────────────────
        _snap_cols = ["account_id", "value_1w", "value_1m", "value_3m",
                      "value_1y", "value_ytd_start"]
        _snap_avail = [c for c in _snap_cols if c in _snap_df.columns]

        if not _snap_df.empty:
            _perf = _live_mv.merge(_snap_df[_snap_avail], on="account_id", how="left")
        else:
            _perf = _live_mv.copy()
            for _c in ["value_1w", "value_1m", "value_3m", "value_1y", "value_ytd_start"]:
                _perf[_c] = float("nan")

        _perf = _perf.merge(_margin_mv, on="account_id", how="left")
        _perf["margin"] = _perf["margin"].fillna(0.0)

        def _ret(cur, prior):
            """Return % change or NaN."""
            if pd.isna(prior) or prior == 0:
                return float("nan")
            return (cur - prior) / prior * 100

        def _chg(cur, prior):
            return float("nan") if pd.isna(prior) else cur - prior

        # ── TOTAL row values ───────────────────────────────────────────────────
        _perf["net_value"] = _perf["current_value"] - _perf["margin"]
        _tot_net   = _perf["net_value"].sum() + _cash_balance

        def _tot_prior(col):
            _valid = _perf[col].dropna()
            return _valid.sum() if not _valid.empty else float("nan")

        # ── Section 1: Portfolio Summary (1-Week) ──────────────────────────────
        st.markdown("##### Portfolio Summary")

        _sum_rows = []
        for _, _r in _perf.iterrows():
            _net  = _r["net_value"]
            _1w   = _r.get("value_1w", float("nan"))
            _sum_rows.append({
                "Account":       _r["account_id"],
                "Current Value": _net,
                "1W Ago":        _1w,
                "$ Change":      _chg(_net, _1w),
                "% Change":      _ret(_net, _1w),
            })

        # Cash & Savings row (stable balance — no historical snapshot)
        if _cash_balance > 0:
            _sum_rows.append({
                "Account":       "CASH",
                "Current Value": _cash_balance,
                "1W Ago":        _cash_balance,
                "$ Change":      0.0,
                "% Change":      0.0,
            })

        _t1w = _tot_prior("value_1w") + (_cash_balance if _cash_balance > 0 else 0.0)
        _sum_rows.append({
            "Account":       "TOTAL",
            "Current Value": _tot_net,
            "1W Ago":        _t1w,
            "$ Change":      _chg(_tot_net, _t1w),
            "% Change":      _ret(_tot_net, _t1w),
        })

        _sum_df = pd.DataFrame(_sum_rows)
        _sum_money = ["Current Value", "1W Ago", "$ Change"]
        _sum_fmt   = {c: "${:,.0f}" for c in _sum_money}
        _sum_fmt["% Change"] = "{:+.2f}%"

        st.dataframe(
            _sum_df.style
                .format(_sum_fmt, na_rep="—")
                .map(colour_cell, subset=["$ Change", "% Change"])
                .apply(_bold_last_row, last_idx=_sum_df.index[-1], axis=1),
            use_container_width=True,
            hide_index=True,
        )

        if _snap_df.empty:
            st.caption("Historical data accumulates with each `ingest.py` run.")

        st.divider()

        # ── Section 2: Portfolio Returns ───────────────────────────────────────
        st.markdown("##### Portfolio Returns")

        _ret_rows = []
        for _, _r in _perf.iterrows():
            _net = _r["net_value"]
            _ret_rows.append({
                "Account": _r["account_id"],
                "1-Week":  _ret(_net, _r.get("value_1w",       float("nan"))),
                "1-Month": _ret(_net, _r.get("value_1m",       float("nan"))),
                "3-Month": _ret(_net, _r.get("value_3m",       float("nan"))),
                "YTD":     _ret(_net, _r.get("value_ytd_start",float("nan"))),
                "1-Year":  _ret(_net, _r.get("value_1y",       float("nan"))),
            })

        _ret_rows.append({
            "Account": "TOTAL",
            "1-Week":  _ret(_tot_net, _tot_prior("value_1w")),
            "1-Month": _ret(_tot_net, _tot_prior("value_1m")),
            "3-Month": _ret(_tot_net, _tot_prior("value_3m")),
            "YTD":     _ret(_tot_net, _tot_prior("value_ytd_start")),
            "1-Year":  _ret(_tot_net, _tot_prior("value_1y")),
        })

        _ret_df   = pd.DataFrame(_ret_rows)
        _ret_cols = ["1-Week", "1-Month", "3-Month", "YTD", "1-Year"]
        _ret_fmt  = {c: "{:+.2f}%" for c in _ret_cols}

        st.dataframe(
            _ret_df.style
                .format(_ret_fmt, na_rep="—")
                .map(colour_cell, subset=_ret_cols)
                .apply(_bold_last_row, last_idx=_ret_df.index[-1], axis=1),
            use_container_width=True,
            hide_index=True,
        )

