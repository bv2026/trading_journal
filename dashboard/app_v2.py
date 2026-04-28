# -*- coding: utf-8 -*-
"""Portfolio Journal v2 — MCP-first redesign.

Run alongside the original:
    streamlit run dashboard/app_v2.py --server.port 8502

Shares the same DB and src/ modules as app.py.
New layout: asset-class breakdown, unified positions by type, broker-level views.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from src.db import DB_PATH, init_db, load_transactions, load_snapshot_periods
from src.metrics import compute_metrics, net_income as _net_income_fn, colour_cell, style_table, _bold_last_row
from src.positions import (
    load_positions_from_db,
    load_options_from_db,
    load_futures_from_db,
    load_crypto_from_db,
    load_all_positions,
    compute_net_worth,
)

# ── Schema init ────────────────────────────────────────────────────────────────
if DB_PATH.exists():
    init_db()

# ── Background ingest on startup ───────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
if "ingest_proc_v2" not in st.session_state:
    st.session_state.ingest_proc_v2 = subprocess.Popen(
        [sys.executable, str(_ROOT / "ingest.py")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(_ROOT),
    )
    st.session_state.ingest_done_v2 = False

_proc: subprocess.Popen = st.session_state.ingest_proc_v2
if not st.session_state.ingest_done_v2 and _proc.poll() is not None:
    st.session_state.ingest_done_v2 = True
    st.cache_data.clear()
    st.rerun()

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Portfolio Journal v2", page_icon="📊", layout="wide")

# ── Colours ────────────────────────────────────────────────────────────────────
C_GREEN  = "#22c55e"
C_RED    = "#ef4444"
C_ORANGE = "#f97316"
C_BLUE   = "#3b82f6"
C_PURPLE = "#a855f7"
ACCOUNT_COLOURS = px.colors.qualitative.Safe

# ── Cached loaders ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def _load_txns() -> pd.DataFrame:
    return load_transactions() if DB_PATH.exists() else pd.DataFrame()

@st.cache_data(ttl=300)
def _load_equity() -> pd.DataFrame:
    return load_positions_from_db()

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
def _load_all() -> pd.DataFrame:
    return load_all_positions()

@st.cache_data(ttl=300)
def _load_snapshots() -> pd.DataFrame:
    return load_snapshot_periods()

# ── Load data ──────────────────────────────────────────────────────────────────
df_all = _load_txns()
if df_all.empty:
    st.error("No data found — run `python ingest.py` first.")
    st.stop()

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📊 Portfolio Journal v2")
    st.badge("v2", color="blue")
    st.divider()

    min_d = df_all["date"].min().date()
    max_d = df_all["date"].max().date()
    date_range = st.date_input("Date range", value=(min_d, max_d),
                               min_value=min_d, max_value=max_d)
    start_d, end_d = date_range if len(date_range) == 2 else (min_d, max_d)

    all_accounts = sorted(df_all["account_id"].unique())
    accounts = st.multiselect("Accounts", all_accounts, default=all_accounts)
    if not accounts:
        accounts = all_accounts

    show_internal = st.checkbox("Include internal transfers", value=False)

    st.divider()
    st.caption(f"DB: `{DB_PATH.name}`")
    if st.session_state.get("ingest_done_v2", False):
        st.success("Data refreshed", icon="✅")
    else:
        st.info("Refreshing data…", icon="🔄")
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

# ── Filter transactions ────────────────────────────────────────────────────────
mask = (
    (df_all["date"].dt.date >= start_d)
    & (df_all["date"].dt.date <= end_d)
    & (df_all["account_id"].isin(accounts))
)
df = df_all[mask].copy()
if not show_internal:
    df = df[df["subcategory"] != "internal_transfer"]
df = df[df["category"] != "other"]

# ── Header ─────────────────────────────────────────────────────────────────────
m_all = compute_metrics(df)
st.title("Portfolio Journal v2")
st.caption(f"{len(df):,} transactions · {start_d} → {end_d} · {len(accounts)} account(s)")

# ── Net Worth banner — all asset classes ───────────────────────────────────────
try:
    _all_pos = _load_all()
    nw = compute_net_worth(_all_pos)

    # Per-asset-class market value
    _eq_mv  = float(_all_pos[_all_pos.get("asset_class", pd.Series()) == "equity"]["MARKET VALUE"].sum()) if not _all_pos.empty else 0.0
    _opt_mv = float(_all_pos[_all_pos.get("asset_class", pd.Series()) == "options"]["MARKET VALUE"].sum()) if not _all_pos.empty else 0.0
    _fut_mv = float(_all_pos[_all_pos.get("asset_class", pd.Series()) == "futures"]["MARKET VALUE"].sum()) if not _all_pos.empty else 0.0
    _cry_mv = float(_all_pos[_all_pos.get("asset_class", pd.Series()) == "crypto"]["MARKET VALUE"].sum()) if not _all_pos.empty else 0.0

    bx1, bx2, bx3, bx4, bx5, bx6 = st.columns(6)
    bx1.metric("Net Worth",      f"${nw['net_worth']:,.0f}")
    bx2.metric("Equity",         f"${_eq_mv:,.0f}")
    bx3.metric("Options",        f"${_opt_mv:,.0f}")
    bx4.metric("Futures",        f"${_fut_mv:,.0f}")
    bx5.metric("Crypto",         f"${_cry_mv:,.0f}")
    bx6.metric("Margin Borrowed",f"${nw['margin']:,.0f}",
               delta=f"-${nw['margin']:,.0f}", delta_color="inverse")
except Exception:
    pass

# ── Global KPI row ─────────────────────────────────────────────────────────────
_kpi_row = {
    "Net Cash Flow":   m_all["net_cash"],
    "Dividends":       m_all["dividends"],
    "Rewards":         m_all["rewards"],
    "Div + Rewards":   m_all["dividends"] + m_all["rewards"],
    "Margin Interest": m_all["margin_int"],
    "Fees":            m_all["fees"],
    "Net Income":      _net_income_fn(m_all),
}
_kpi_df = pd.DataFrame([_kpi_row])
st.dataframe(
    _kpi_df.style
        .format({c: "${:,.2f}" for c in _kpi_df.columns})
        .map(colour_cell, subset=list(_kpi_df.columns)),
    use_container_width=True, hide_index=True,
)
st.divider()

# ── Tabs ───────────────────────────────────────────────────────────────────────
(tab_portfolio, tab_yearly, tab_breakdown,
 tab_positions, tab_txns, tab_perf) = st.tabs([
    "Portfolio", "Yearly Summary", "By Account",
    "Positions", "Transactions", "Performance",
])


# ═══ TAB 1 — Portfolio ════════════════════════════════════════════════════════
with tab_portfolio:

    eq       = _load_equity()
    opts     = _load_options()
    futs     = _load_futures()
    crypto   = _load_crypto()

    has_eq   = not eq.empty
    has_opts = not opts.empty
    has_futs = not futs.empty
    has_cry  = not crypto.empty

    if has_eq:
        _is_margin = eq["Ticker"].str.upper() == "MARGIN"
        margin_df  = eq[_is_margin].copy()
        pos        = eq[~_is_margin].copy()
        for col in ["PRICE", "Shares", "Cost_Basis", "COST", "MARKET VALUE", "totalReturn"]:
            if col in pos.columns:
                pos[col] = pd.to_numeric(pos[col], errors="coerce")
        margin_df["MARKET VALUE"] = pd.to_numeric(margin_df["MARKET VALUE"], errors="coerce")

    # ── 1. ACCOUNT SUMMARY TABLE ───────────────────────────────────────────────
    st.subheader("Account Summary")

    # Position MV + cost per account (equity)
    if has_eq:
        _pos_by_acct = (
            pos.groupby("Account")
               .agg(Positions   =("Ticker",       "count"),
                    Market_Value=("MARKET VALUE", "sum"),
                    Total_Cost  =("COST",         "sum"),
                    PnL         =("totalReturn",  "sum"))
               .reset_index()
        )
        _margin_by_acct = (
            margin_df.groupby("Account")["MARKET VALUE"].sum()
                     .abs().reset_index()
                     .rename(columns={"MARKET VALUE": "Margin"})
        )
        # Options MV per account
        _opt_by_acct = (
            opts.groupby("Account")["MARKET VALUE"].sum()
                .reset_index().rename(columns={"MARKET VALUE": "Options_MV"})
        ) if has_opts else pd.DataFrame(columns=["Account", "Options_MV"])
        # Futures MV per account
        _fut_by_acct = (
            futs.groupby("Account")["MARKET VALUE"].sum()
                .reset_index().rename(columns={"MARKET VALUE": "Futures_MV"})
        ) if has_futs else pd.DataFrame(columns=["Account", "Futures_MV"])

    tx_rows = []
    for acct in [a for a in all_accounts if a in accounts]:
        ad = df[df["account_id"] == acct]
        if ad.empty:
            continue
        am = compute_metrics(ad)
        tx_rows.append({
            "Account":    acct,
            "Broker":     ad["broker"].iloc[0],
            "Net Cash":   am["net_cash"],
            "Dividends":  am["dividends"],
            "Rewards":    am["rewards"],
            "Margin Int": am["margin_int"],
            "Fees":       am["fees"],
            "Net Income": _net_income_fn(am),
        })
    summary = pd.DataFrame(tx_rows).fillna(0)

    if has_eq and not summary.empty:
        summary = (summary
            .merge(_pos_by_acct,    on="Account", how="left")
            .merge(_margin_by_acct, on="Account", how="left")
            .merge(_opt_by_acct,    on="Account", how="left")
            .merge(_fut_by_acct,    on="Account", how="left")
        )
        for col in ["Positions", "Market_Value", "Total_Cost", "PnL",
                    "Margin", "Options_MV", "Futures_MV"]:
            summary[col] = summary.get(col, 0).fillna(0)
        summary["Positions"]  = summary["Positions"].astype(int)
        summary["Margin"]     = summary["Margin"].abs()
        summary["Return_%"]   = (
            summary["PnL"] / summary["Total_Cost"].replace(0, float("nan")) * 100
        ).fillna(0).round(2)

        _t = {
            "Account":    "TOTAL", "Broker": "",
            "Positions":  int(summary["Positions"].sum()),
            "Market_Value": summary["Market_Value"].sum(),
            "Total_Cost": summary["Total_Cost"].sum(),
            "PnL":        summary["PnL"].sum(),
            "Return_%":   (summary["PnL"].sum() / summary["Total_Cost"].sum() * 100
                           if summary["Total_Cost"].sum() else 0),
            "Margin":     summary["Margin"].sum(),
            "Options_MV": summary["Options_MV"].sum(),
            "Futures_MV": summary["Futures_MV"].sum(),
            "Net Cash":   summary["Net Cash"].sum(),
            "Dividends":  summary["Dividends"].sum(),
            "Rewards":    summary["Rewards"].sum(),
            "Margin Int": summary["Margin Int"].sum(),
            "Fees":       summary["Fees"].sum(),
            "Net Income": summary["Net Income"].sum(),
        }
        summary = pd.concat([summary, pd.DataFrame([_t])], ignore_index=True)

        disp_cols  = ["Account", "Broker", "Market_Value", "Options_MV", "Futures_MV",
                      "Total_Cost", "PnL", "Return_%", "Margin",
                      "Net Cash", "Dividends", "Rewards", "Margin Int", "Fees", "Net Income"]
        money_cols = ["Market_Value", "Options_MV", "Futures_MV", "Total_Cost", "PnL",
                      "Margin", "Net Cash", "Dividends", "Rewards", "Margin Int", "Fees", "Net Income"]
        fmt = {c: "${:,.0f}" for c in money_cols}
        fmt["Return_%"] = "{:+.1f}%"

        st.dataframe(
            summary[disp_cols].style
                .format(fmt)
                .map(colour_cell, subset=["PnL", "Return_%", "Net Cash",
                                          "Dividends", "Rewards", "Margin Int",
                                          "Fees", "Net Income"])
                .apply(_bold_last_row, last_idx=summary.index[-1], axis=1),
            use_container_width=True, hide_index=True,
        )

    st.divider()

    # ── 2. SECTOR ALLOCATION (equity only) ────────────────────────────────────
    if has_eq:
        total_mv = pos["MARKET VALUE"].sum()
        st.subheader("Sector Allocation — Equity")

        import re as _re
        _INCOME_ETF_RE = _re.compile(r"\b(yieldmax|roundhill|defiance|rex)\b", _re.IGNORECASE)
        _sec_display = pos["sector"].copy() if "sector" in pos.columns else pd.Series("Unknown", index=pos.index)
        if "TYPE" in pos.columns:
            _is_etf = pos["TYPE"].str.upper().eq("ETF") & (_sec_display != "Income ETF")
            _sec_display = _sec_display.where(~_is_etf, "ETF")

        sec_grp = (
            pd.Series(_sec_display.values, name="sector").to_frame()
              .assign(mv=pos["MARKET VALUE"].values)
              .groupby("sector")["mv"].sum()
              .sort_values(ascending=False).reset_index()
              .rename(columns={"mv": "MARKET VALUE"})
        )
        fig_sec = px.pie(sec_grp, values="MARKET VALUE", names="sector",
                         hole=0.4, color_discrete_sequence=px.colors.qualitative.Safe)
        fig_sec.update_traces(textposition="inside", textinfo="percent+label",
                               hovertemplate="%{label}<br>$%{value:,.0f}<br>%{percent}")
        fig_sec.update_layout(showlegend=False, margin=dict(t=10, b=10))
        st.plotly_chart(fig_sec, use_container_width=True)
        st.divider()

    # ── 3. POSITIONS BY ACCOUNT (equity expanders) ────────────────────────────
    if has_eq:
        st.subheader("Equity Positions by Account")
        _pos_fmt = {
            "PRICE": "${:.2f}", "Cost_Basis": "${:.4f}", "COST": "${:,.2f}",
            "MARKET VALUE": "${:,.2f}", "totalReturn": "${:+,.2f}",
            "Return_%": "{:+.2f}%", "Shares": "{:,.4f}",
        }
        _pos_cols = ["Ticker", "Name", "TYPE", "sector", "Shares",
                     "PRICE", "Cost_Basis", "COST", "MARKET VALUE",
                     "totalReturn", "Return_%"]

        acct_order = (
            summary[summary["Account"] != "TOTAL"]
            .sort_values("Market_Value", ascending=False)["Account"].tolist()
        )
        for acct in acct_order:
            acct_pos = pos[pos["Account"] == acct].copy()
            if acct_pos.empty:
                continue
            acct_mv    = acct_pos["MARKET VALUE"].sum()
            acct_pnl   = acct_pos["totalReturn"].sum()
            acct_ret   = (acct_pnl / acct_pos["COST"].sum() * 100 if acct_pos["COST"].sum() else 0)
            acct_margin= abs(float(margin_df[margin_df["Account"] == acct]["MARKET VALUE"].sum()))
            # Options for this account
            acct_opts  = opts[opts["Account"] == acct] if has_opts else pd.DataFrame()
            label = (
                f"**{acct}** — {len(acct_pos)} equity · "
                f"MV ${acct_mv:,.0f} · P&L ${acct_pnl:+,.0f} ({acct_ret:+.1f}%) · "
                f"Margin ${acct_margin:,.0f}"
                + (f" · Options ${acct_opts['MARKET VALUE'].sum():,.0f}" if not acct_opts.empty else "")
            )
            with st.expander(label, expanded=False):
                _cost_safe    = acct_pos["COST"].replace(0, float("nan"))
                acct_pos["Return_%"] = (acct_pos["totalReturn"] / _cost_safe * 100).fillna(0).round(2)
                show = [c for c in _pos_cols if c in acct_pos.columns]
                st.dataframe(
                    acct_pos[show].sort_values("MARKET VALUE", ascending=False)
                        .reset_index(drop=True)
                        .style.format({k: v for k, v in _pos_fmt.items() if k in show})
                        .map(colour_cell, subset=["totalReturn", "Return_%"]),
                    use_container_width=True, hide_index=True,
                )
                # Options sub-table inside expander
                if not acct_opts.empty:
                    st.markdown("**Options**")
                    opt_show = [c for c in ["Ticker", "underlying", "expiry", "strike",
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
    if has_eq:
        _sec_tbl_src = pos.copy()
        if "sector" in pos.columns:
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

        _pos_with_collapsed = pos[["Ticker", "sector"]].copy()
        if "sector" in pos.columns:
            _pos_with_collapsed["sector"] = _sec_display.values
        _sec_divs = (
            df_all[df_all["category"] == "dividend"]
            .merge(_pos_with_collapsed.drop_duplicates("Ticker"),
                   left_on="symbol", right_on="Ticker", how="inner")
            .groupby("sector")["amount"].sum().reset_index()
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

    # ── 5. OPTIONS SUMMARY ─────────────────────────────────────────────────────
    if has_opts:
        st.divider()
        st.subheader("Options Summary")
        import datetime as _odt
        _today = _odt.date.today()
        _opt_mv_total = opts["MARKET VALUE"].sum()
        _opt_count    = len(opts)

        oc1, oc2, oc3 = st.columns(3)
        oc1.metric("Open Positions", _opt_count)
        oc2.metric("Total Market Value", f"${_opt_mv_total:,.0f}")
        if "expiry" in opts.columns:
            _expiring = opts[pd.to_datetime(opts["expiry"], errors="coerce").dt.date
                             <= (_today + _odt.timedelta(days=7))]
            oc3.metric("Expiring This Week", len(_expiring))

        opt_disp_cols = [c for c in ["Account", "Ticker", "underlying", "expiry",
                                      "strike", "call_put", "qty", "price", "MARKET VALUE"]
                         if c in opts.columns]
        st.dataframe(
            opts[opt_disp_cols].sort_values("expiry" if "expiry" in opts.columns else opt_disp_cols[0])
                .reset_index(drop=True)
                .style.format({"MARKET VALUE": "${:,.2f}", "price": "${:.2f}",
                                "strike": "${:.2f}"}),
            use_container_width=True, hide_index=True,
        )

    # ── 6. FUTURES SUMMARY ─────────────────────────────────────────────────────
    if has_futs:
        st.divider()
        st.subheader("Futures Summary")
        _fut_mv_total = futs["MARKET VALUE"].sum()
        fc1, fc2 = st.columns(2)
        fc1.metric("Open Contracts", len(futs))
        fc2.metric("Total Market Value", f"${_fut_mv_total:,.0f}")

        fut_disp_cols = [c for c in ["Account", "Ticker", "qty", "price", "MARKET VALUE"]
                         if c in futs.columns]
        st.dataframe(
            futs[fut_disp_cols].reset_index(drop=True)
                .style.format({"MARKET VALUE": "${:,.2f}", "price": "${:.4f}"}),
            use_container_width=True, hide_index=True,
        )


# ═══ TAB 2 — Yearly Summary (unchanged) ═══════════════════════════════════════
with tab_yearly:
    import datetime as _yrdt

    df["year"] = df["date"].dt.year
    _yr_curr  = _yrdt.date.today().year
    _yr_prev  = _yr_curr - 1
    _yr_avail = set(df["year"].dropna().unique().astype(int))
    _yr_cols  = [y for y in [_yr_prev, _yr_curr] if y in _yr_avail]

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
            _inc_pivot[_inc_yr_cols + ["ALL"]].reset_index()
            .rename(columns={"subcategory": "Type"})
            .sort_values("ALL", ascending=False).reset_index(drop=True)
        )
        _inc_val_cols = [c for c in _inc_tbl.columns if c != "Type"]
        st.dataframe(
            _inc_tbl.style
                .format({c: "${:,.0f}" for c in _inc_val_cols})
                .map(colour_cell, subset=_inc_val_cols),
            use_container_width=True, hide_index=True,
        )


# ═══ TAB 3 — By Account (unchanged) ══════════════════════════════════════════
with tab_breakdown:
    import datetime as _dt

    _curr_yr = _dt.date.today().year
    _prev_yr = _curr_yr - 1
    df_acct  = df.copy()
    df_acct["year"] = df_acct["date"].dt.year
    _available  = set(df_acct["year"].dropna().unique().astype(int))
    _pivot_years = [y for y in [_prev_yr, _curr_yr] if y in _available]

    def _pivot(source: pd.DataFrame, metric_fn) -> pd.DataFrame:
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

    _show_pivot(_pivot(df_acct, lambda m: m["net_cash"]),                 "Net Cash Flow by Account")
    st.divider()
    _show_pivot(_pivot(df_acct, lambda m: m["dividends"] + m["rewards"]), "Div + Rewards by Account")
    st.divider()
    _show_pivot(_pivot(df_acct, lambda m: m["margin_int"] + m["fees"]),   "Margin + Fees by Account")

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
            in_rows = [{"Type": LABELS[s],
                        "Amount": float(crypto_df[crypto_df["subcategory"] == s]["amount"].sum()),
                        "Txns": int((crypto_df["subcategory"] == s).sum())}
                       for s in INFLOW_SUBS]
            in_df = pd.DataFrame(in_rows)
            in_df.loc[len(in_df)] = ["Total In", total_in, ""]
            st.dataframe(in_df.style.format({"Amount": "${:,.2f}"})
                             .apply(_bold_last_row, last_idx=in_df.index[-1], axis=1),
                         use_container_width=True, hide_index=True)
        with col_out:
            st.markdown("**Outflows**")
            out_rows = [{"Type": LABELS[s],
                         "Amount": float(crypto_df[crypto_df["subcategory"] == s]["amount"].sum()),
                         "Txns": int((crypto_df["subcategory"] == s).sum())}
                        for s in OUTFLOW_SUBS]
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


# ═══ TAB 4 — Positions v2 (4 sub-tables) ═════════════════════════════════════
with tab_positions:

    eq_pos   = _load_equity()
    opt_pos  = _load_options()
    fut_pos  = _load_futures()
    cry_pos  = _load_crypto()

    # Broker filter
    all_brokers = sorted(df_all["broker"].dropna().unique())
    sel_brokers = st.multiselect("Broker filter", all_brokers, default=all_brokers,
                                  key="pos_broker_filter")

    # Map account → broker for filtering positions
    _acct_broker = df_all[["account_id", "broker"]].drop_duplicates().set_index("account_id")["broker"]

    def _filter_by_broker(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty or "Account" not in frame.columns:
            return frame
        keep = frame["Account"].map(_acct_broker).isin(sel_brokers)
        return frame[keep].copy()

    eq_pos  = _filter_by_broker(eq_pos)
    opt_pos = _filter_by_broker(opt_pos)
    fut_pos = _filter_by_broker(fut_pos)
    cry_pos = _filter_by_broker(cry_pos)

    # ── Equity sub-table ───────────────────────────────────────────────────────
    if not eq_pos.empty:
        _eq = eq_pos[eq_pos["Ticker"].str.upper() != "MARGIN"].copy()
        for _c in ["COST", "MARKET VALUE", "totalReturn"]:
            _eq[_c] = pd.to_numeric(_eq[_c], errors="coerce")

        sym = (
            _eq.groupby(["Ticker"] + (["Name"] if "Name" in _eq.columns else [])
                        + (["sector"] if "sector" in _eq.columns else []))
               .agg(Market_Value=("MARKET VALUE", "sum"),
                    Total_Cost  =("COST",         "sum"),
                    PnL         =("totalReturn",  "sum"))
               .reset_index()
               .sort_values("Market_Value", ascending=False)
        )
        sym["Return_%"] = (sym["PnL"] / sym["Total_Cost"].replace(0, float("nan")) * 100).round(2)
        _divs = (
            df_all[df_all["category"] == "dividend"]
            .groupby("symbol")["amount"].sum().reset_index()
            .rename(columns={"symbol": "Ticker", "amount": "Dividends"})
        )
        sym = sym.merge(_divs, on="Ticker", how="left")
        sym["Dividends"] = sym["Dividends"].fillna(0)

        _t_mv   = sym["Market_Value"].sum()
        _t_cost = sym["Total_Cost"].sum()
        _t_pnl  = sym["PnL"].sum()
        _t_ret  = (_t_pnl / _t_cost * 100 if _t_cost else 0)
        _t_div  = sym["Dividends"].sum()

        eq_show_cols = [c for c in ["Ticker", "Name", "sector", "Market_Value",
                                     "Total_Cost", "PnL", "Return_%", "Dividends"]
                        if c in sym.columns]
        eq_fmt = {c: "${:,.2f}" for c in ["Market_Value", "Total_Cost", "PnL", "Dividends"]}
        eq_fmt["Return_%"] = "{:+.2f}%"

        st.subheader(f"Equity — {len(sym)} holdings")
        st.dataframe(
            sym[eq_show_cols].style.format(eq_fmt).map(colour_cell, subset=["PnL", "Return_%"]),
            use_container_width=True, hide_index=True,
        )
        st.markdown("<hr style='margin:4px 0; border-color:#6b7280'>", unsafe_allow_html=True)
        fc1, fc2, fc3, fc4, fc5 = st.columns(5)
        fc1.metric("Market Value", f"${_t_mv:,.0f}")
        fc2.metric("Total Cost",   f"${_t_cost:,.0f}")
        fc3.metric("P&L",          f"${_t_pnl:+,.0f}")
        fc4.metric("Return",       f"{_t_ret:+.2f}%", delta=f"{_t_ret:+.2f}%")
        fc5.metric("Dividends",    f"${_t_div:,.0f}")

    # ── Options sub-table ──────────────────────────────────────────────────────
    if not opt_pos.empty:
        st.divider()
        opt_mv  = opt_pos["MARKET VALUE"].sum()
        opt_show = [c for c in ["Account", "Ticker", "underlying", "expiry",
                                  "strike", "call_put", "qty", "price", "MARKET VALUE"]
                    if c in opt_pos.columns]
        st.subheader(f"Options — {len(opt_pos)} positions · MV ${opt_mv:,.0f}")
        opt_sort = "expiry" if "expiry" in opt_pos.columns else opt_show[0]
        st.dataframe(
            opt_pos[opt_show].sort_values(opt_sort).reset_index(drop=True)
                .style.format({"MARKET VALUE": "${:,.2f}", "price": "${:.2f}",
                                "strike": "${:.2f}"}),
            use_container_width=True, hide_index=True,
        )
        ot1, ot2 = st.columns(2)
        ot1.metric("Total Market Value", f"${opt_mv:,.0f}")
        ot2.metric("Positions", len(opt_pos))

    # ── Futures sub-table ──────────────────────────────────────────────────────
    if not fut_pos.empty:
        st.divider()
        fut_mv   = fut_pos["MARKET VALUE"].sum()
        fut_show = [c for c in ["Account", "Ticker", "qty", "price", "MARKET VALUE"]
                    if c in fut_pos.columns]
        st.subheader(f"Futures — {len(fut_pos)} contracts · MV ${fut_mv:,.0f}")
        st.dataframe(
            fut_pos[fut_show].reset_index(drop=True)
                .style.format({"MARKET VALUE": "${:,.2f}", "price": "${:.4f}"}),
            use_container_width=True, hide_index=True,
        )
        ft1, ft2 = st.columns(2)
        ft1.metric("Total Market Value", f"${fut_mv:,.0f}")
        ft2.metric("Contracts", len(fut_pos))

    # ── Crypto sub-table ───────────────────────────────────────────────────────
    if not cry_pos.empty:
        st.divider()
        cry_mv   = cry_pos["MARKET VALUE"].sum()
        cry_show = [c for c in ["Account", "Ticker", "qty", "price",
                                  "cost_basis", "MARKET VALUE"]
                    if c in cry_pos.columns]
        st.subheader(f"Crypto — {len(cry_pos)} holdings · MV ${cry_mv:,.0f}")
        st.dataframe(
            cry_pos[cry_show].sort_values("MARKET VALUE", ascending=False)
                .reset_index(drop=True)
                .style.format({"MARKET VALUE": "${:,.2f}", "price": "${:.4f}",
                                "cost_basis": "${:.4f}"}),
            use_container_width=True, hide_index=True,
        )
        ct1, ct2 = st.columns(2)
        ct1.metric("Total Market Value", f"${cry_mv:,.0f}")
        ct2.metric("Holdings", len(cry_pos))

    if eq_pos.empty and opt_pos.empty and fut_pos.empty and cry_pos.empty:
        st.info("No positions data. Run `python ingest.py` to populate.")


# ═══ TAB 5 — Transactions ═════════════════════════════════════════════════════
with tab_txns:
    col1, col2, col3, col4 = st.columns([2, 2, 2, 3])
    with col1:
        cat_filter = st.multiselect("Category", sorted(df["category"].unique()),
                                     default=sorted(df["category"].unique()))
    with col2:
        # Broker filter instead of account filter
        _brokers_in_df = sorted(df["broker"].dropna().unique()) if "broker" in df.columns else []
        broker_filter  = st.multiselect("Broker", _brokers_in_df, default=_brokers_in_df)
    with col3:
        yr_filter = st.multiselect("Year",
                                    sorted(df["date"].dt.year.unique().astype(int), reverse=True),
                                    default=[])
    with col4:
        search = st.text_input("Search description", placeholder="AAPL, margin, staking …")

    txn = df[df["category"].isin(cat_filter)].copy()
    if "broker" in txn.columns:
        txn = txn[txn["broker"].isin(broker_filter)]
    if yr_filter:
        txn = txn[txn["date"].dt.year.isin(yr_filter)]
    if search:
        txn = txn[txn["description"].str.contains(search, case=False, na=False)]

    display_cols = ["date", "account_id", "broker", "category", "subcategory",
                    "amount", "currency", "symbol", "description"]
    display_cols = [c for c in display_cols if c in txn.columns]
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


# ═══ TAB 6 — Performance (unchanged logic, updated to use all positions) ══════
with tab_perf:
    st.subheader("Portfolio Performance")
    try:
        _all_pos  = _load_all()
        _snap_df  = _load_snapshots()
    except Exception as _exc:
        st.error(f"Failed to load performance data: {_exc}")
        _all_pos  = pd.DataFrame()
        _snap_df  = pd.DataFrame()

    if _all_pos.empty:
        st.info("No positions loaded yet. Run `python ingest.py` to populate.")
    else:
        _is_margin_p = _all_pos["Ticker"].str.upper() == "MARGIN"
        _live_mv = (
            _all_pos[~_is_margin_p].groupby("Account")["MARKET VALUE"].sum()
            .reset_index().rename(columns={"Account": "account_id", "MARKET VALUE": "current_value"})
        )
        _live_mv["current_value"] = pd.to_numeric(_live_mv["current_value"], errors="coerce")
        _margin_mv = (
            _all_pos[_is_margin_p].groupby("Account")["MARKET VALUE"].sum().abs()
            .reset_index().rename(columns={"Account": "account_id", "MARKET VALUE": "margin"})
        )

        _snap_cols  = ["account_id", "value_1w", "value_1m", "value_3m", "value_1y", "value_ytd_start"]
        _snap_avail = [c for c in _snap_cols if c in _snap_df.columns]
        _perf = (_live_mv.merge(_snap_df[_snap_avail], on="account_id", how="left")
                 if not _snap_df.empty else _live_mv.copy())
        for _c in ["value_1w", "value_1m", "value_3m", "value_1y", "value_ytd_start"]:
            if _c not in _perf.columns:
                _perf[_c] = float("nan")
        _perf = _perf.merge(_margin_mv, on="account_id", how="left")
        _perf["margin"]    = _perf["margin"].fillna(0.0)
        _perf["net_value"] = _perf["current_value"] - _perf["margin"]

        def _ret(cur, prior):
            return float("nan") if pd.isna(prior) or prior == 0 else (cur - prior) / prior * 100

        def _chg(cur, prior):
            return float("nan") if pd.isna(prior) else cur - prior

        _tot_net = _perf["net_value"].sum()

        def _tot_prior(col):
            _v = _perf[col].dropna()
            return _v.sum() if not _v.empty else float("nan")

        # Section 1: Summary (1-week)
        st.markdown("##### Portfolio Summary")
        _sum_rows = [{"Account": _r["account_id"],
                       "Current Value": _r["net_value"],
                       "1W Ago": _r.get("value_1w", float("nan")),
                       "$ Change": _chg(_r["net_value"], _r.get("value_1w", float("nan"))),
                       "% Change": _ret(_r["net_value"], _r.get("value_1w", float("nan")))}
                      for _, _r in _perf.iterrows()]
        _t1w = _tot_prior("value_1w")
        _sum_rows.append({"Account": "TOTAL", "Current Value": _tot_net,
                           "1W Ago": _t1w, "$ Change": _chg(_tot_net, _t1w),
                           "% Change": _ret(_tot_net, _t1w)})
        _sum_df  = pd.DataFrame(_sum_rows)
        _sum_fmt = {c: "${:,.0f}" for c in ["Current Value", "1W Ago", "$ Change"]}
        _sum_fmt["% Change"] = "{:+.2f}%"
        st.dataframe(
            _sum_df.style.format(_sum_fmt, na_rep="—")
                .map(colour_cell, subset=["$ Change", "% Change"])
                .apply(_bold_last_row, last_idx=_sum_df.index[-1], axis=1),
            use_container_width=True, hide_index=True,
        )
        if _snap_df.empty:
            st.caption("Historical data accumulates with each `ingest.py` run.")
        st.divider()

        # Section 2: Returns
        st.markdown("##### Portfolio Returns")
        _ret_rows = [{"Account": _r["account_id"],
                       "1-Week":  _ret(_r["net_value"], _r.get("value_1w",        float("nan"))),
                       "1-Month": _ret(_r["net_value"], _r.get("value_1m",        float("nan"))),
                       "3-Month": _ret(_r["net_value"], _r.get("value_3m",        float("nan"))),
                       "YTD":     _ret(_r["net_value"], _r.get("value_ytd_start", float("nan"))),
                       "1-Year":  _ret(_r["net_value"], _r.get("value_1y",        float("nan")))}
                      for _, _r in _perf.iterrows()]
        _ret_rows.append({"Account": "TOTAL",
                           "1-Week":  _ret(_tot_net, _tot_prior("value_1w")),
                           "1-Month": _ret(_tot_net, _tot_prior("value_1m")),
                           "3-Month": _ret(_tot_net, _tot_prior("value_3m")),
                           "YTD":     _ret(_tot_net, _tot_prior("value_ytd_start")),
                           "1-Year":  _ret(_tot_net, _tot_prior("value_1y"))})
        _ret_df   = pd.DataFrame(_ret_rows)
        _ret_cols = ["1-Week", "1-Month", "3-Month", "YTD", "1-Year"]
        st.dataframe(
            _ret_df.style.format({c: "{:+.2f}%" for c in _ret_cols}, na_rep="—")
                .map(colour_cell, subset=_ret_cols)
                .apply(_bold_last_row, last_idx=_ret_df.index[-1], axis=1),
            use_container_width=True, hide_index=True,
        )
