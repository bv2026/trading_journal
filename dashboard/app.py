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
from src.db import (DB_PATH, init_db, load_transactions, load_snapshot_periods,
                    get_cash_balance, get_accounts_by_type, upsert_cash_balance,
                    load_account_settings, save_account_settings,
                    load_account_balances)

# Ensure schema is up-to-date (creates new tables/views if this is an existing DB).
if DB_PATH.exists():
    init_db()

# Dashboard refresh state. Position syncs are explicit MCP/CLI actions; opening
# the dashboard should not start a CSV ingest or mutate the database.
_ROOT = Path(__file__).parent.parent
if "data_refreshed" not in st.session_state:
    st.session_state.data_refreshed = False
from src.metrics import compute_metrics, net_income as _net_income_fn, colour_cell, style_table, _bold_last_row
from src.positions import (
    load_positions_from_db, compute_net_worth, load_all_positions,
    load_options_from_db, load_futures_from_db, load_crypto_from_db,
)
from src.mcp_tools.health import check_mcp_health
from src.services import dashboard_performance as performance_tab
from src.services import dashboard_portfolio as portfolio_tab
from src.services import dashboard_positions as positions_tab
from src.services import dashboard_transactions as transaction_tabs

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


@st.cache_data(ttl=300)
def _load_account_balances() -> pd.DataFrame:
    """Load latest broker/account balance rows persisted by the CLI."""
    return load_account_balances()


@st.cache_data(ttl=60)
def _load_mcp_health() -> pd.DataFrame:
    return pd.DataFrame(check_mcp_health())

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
    if st.session_state.get("data_refreshed", False):
        st.success("Data refreshed", icon="✅")
    if st.button("🔄 Refresh"):
        st.session_state.data_refreshed = True
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

m_all = compute_metrics(df)
_cash_balance = _load_cash_balance()

st.title("Portfolio Journal")
st.caption(f"{len(df):,} transactions · {start_d} → {end_d} · {len(accounts)} account(s)")

st.divider()

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab_portfolio, tab_yearly, tab_breakdown, tab_positions, tab_txns, tab_perf, tab_brokers, tab_settings = st.tabs([
    "Portfolio", "Yearly Summary", "By Account", "Positions", "Transactions", "Performance", "Broker MCP", "Settings"
])


# ═══ TAB 1 — Portfolio ════════════════════════════════════════════════════════
with tab_portfolio:
    _acct_balances = _load_account_balances()

    # ── Net Worth banner ───────────────────────────────────────────────────────
    try:
        _nw_data = portfolio_tab.net_worth_banner(
            account_balances=_acct_balances,
            all_positions=_load_all_positions(),
            cash_balance=_cash_balance,
        )
        if _nw_data["market_value"]:
            nw1, nw2, nw3 = st.columns(3)
            nw1.metric("Net Worth",       f"${_nw_data['net_worth']:,.0f}")
            nw2.metric("Market Value",    f"${_nw_data['market_value']:,.0f}")
            nw3.metric("Margin Borrowed", f"${_nw_data['margin']:,.0f}",
                       delta=f"-${_nw_data['margin']:,.0f}", delta_color="inverse")
    except Exception:
        pass

    _kpi_row = portfolio_tab.portfolio_kpi_row(m_all)
    _kpi_df = pd.DataFrame([_kpi_row])
    st.dataframe(
        _kpi_df.style
            .format({c: "${:,.0f}" for c in _kpi_df.columns})
            .map(colour_cell, subset=list(_kpi_df.columns)),
        use_container_width=True,
        hide_index=True,
    )

    st.divider()

    # ── Load positions data ────────────────────────────────────────────────────
    pos_all  = _load_positions()
    opts_all = _load_options()
    futs_all = _load_futures()
    cry_all  = _load_crypto()
    has_positions = not pos_all.empty
    has_opts = not opts_all.empty
    has_futs = not futs_all.empty
    has_crypto = not cry_all.empty

    if has_positions:
        pos, margin_df = portfolio_tab.split_equity_margin(pos_all)
    else:
        pos = pd.DataFrame()
        margin_df = pd.DataFrame()

    # ── 1. ACCOUNT SUMMARY ────────────────────────────────────────────────────
    st.subheader("Account Summary")
    if has_positions:
        _acct_sum = portfolio_tab.account_summary(
            pos=pos,
            margin_df=margin_df,
            opts_all=opts_all,
            futs_all=futs_all,
            cry_all=cry_all,
            account_balances=_acct_balances,
            transactions=df_all,
            all_accounts=all_accounts,
            selected_accounts=accounts,
            cash_balance=_cash_balance,
        )
        _money_acct = ["Market Value", "Cost Basis", "Margin", "Net Equity"]
        st.dataframe(
            _acct_sum.style
                .format({c: "${:,.0f}" for c in _money_acct}, na_rep="")
                .map(colour_cell, subset=["Net Equity"])
                .apply(_bold_last_row, last_idx=_acct_sum.index[-1], axis=1),
            use_container_width=True, hide_index=True,
        )

        st.divider()

        # ── 2. ASSET CLASS BREAKDOWN ──────────────────────────────────────────
        st.subheader("Asset Class Breakdown")
        _asset_df = portfolio_tab.asset_class_breakdown(
            pos=pos,
            opts_all=opts_all,
            futs_all=futs_all,
            cry_all=cry_all,
            cash_balance=_cash_balance,
            crypto_accounts=set(get_accounts_by_type("crypto")),
        )
        st.dataframe(
            _asset_df.style
                .format({"Market Value": "${:,.0f}", "Allocation": "{:.1f}%"})
                .apply(_bold_last_row, last_idx=_asset_df.index[-1], axis=1),
            use_container_width=True, hide_index=True,
        )

        st.divider()

        # ── 3. FUTURES BY COMMODITY ───────────────────────────────────────────
        if has_futs:
            st.subheader("Futures by Commodity")
            _fut_by_commodity = portfolio_tab.futures_by_commodity(futs_all)
            st.dataframe(
                _fut_by_commodity.style
                    .format({"Net_MV": "${:+,.0f}"})
                    .map(colour_cell, subset=["Net_MV"]),
                use_container_width=True, hide_index=True,
            )
            st.divider()

    # ── 4. SECTOR ALLOCATION CHART ────────────────────────────────────────────
    if has_positions:
        total_mv = pos["MARKET VALUE"].sum()
        _sec_display = portfolio_tab.collapsed_sector_labels(pos)

        st.subheader("Sector Allocation")
        sec_grp = portfolio_tab.sector_allocation(pos, _sec_display)
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

    # ── 5. POSITIONS BY ACCOUNT ────────────────────────────────────────────────
    if has_positions:
        st.subheader("Positions by Account")

        _pos_fmt = {
            "PRICE":        "${:.2f}",
            "Cost_Basis":   "${:.4f}",
            "COST":         "${:,.0f}",
            "MARKET VALUE": "${:,.0f}",
            "totalReturn":  "${:+,.0f}",
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
            _acct_sum[_acct_sum["Account"] != "TOTAL"]
            .sort_values("Market Value", ascending=False)["Account"]
            .tolist()
        )

        for acct in acct_order:
            acct_pos = pos[pos["Account"] == acct].copy()
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
                        .style.format(fmt, na_rep="")
                        .map(colour_cell, subset=["totalReturn", "Return_%"]),
                    use_container_width=True,
                    hide_index=True,
                )
                if not acct_opts.empty:
                    st.markdown("**Options**")
                    opt_show = [c for c in ["symbol", "underlying", "expiry", "strike",
                                            "call_put", "qty", "price", "MARKET VALUE"]
                                if c in acct_opts.columns]
                    _opt_df = acct_opts[opt_show].reset_index(drop=True)
                    _opt_fmt = {}
                    if "MARKET VALUE" in _opt_df.columns:
                        _opt_fmt["MARKET VALUE"] = lambda x: f"${x:,.2f}" if x is not None and x == x else ""
                    if "price" in _opt_df.columns:
                        _opt_fmt["price"] = lambda x: f"${x:.2f}" if x is not None and x == x else ""
                    if "strike" in _opt_df.columns:
                        _opt_fmt["strike"] = lambda x: f"${x:.2f}" if x is not None and x == x else ""
                    st.dataframe(
                        _opt_df.style.format(_opt_fmt, na_rep=""),
                        use_container_width=True, hide_index=True,
                    )

        st.divider()

    # ── 6. SECTOR SUMMARY TABLE ────────────────────────────────────────────────
    if has_positions:
        sec_tbl = portfolio_tab.sector_summary(
            pos=pos,
            transactions=df_all,
            sector_labels=_sec_display,
        )

        st.subheader("Sector Summary")
        st.dataframe(
            sec_tbl.style
                .format({"Market_Value": "${:,.0f}", "Total_Cost": "${:,.0f}",
                         "PnL": "${:+,.0f}", "Alloc_%": "{:.2f}%",
                         "Return_%": "{:+.2f}%", "Dividends": "${:,.0f}"})
                .map(colour_cell, subset=["PnL", "Return_%", "Dividends"]),
            use_container_width=True, hide_index=True,
        )


# ═══ TAB 2 — Yearly Summary ═══════════════════════════════════════════════════
with tab_yearly:
    yr_df = transaction_tabs.yearly_summary_table(df)
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
    _inc_tbl = transaction_tabs.income_breakdown_by_type(df)
    if not _inc_tbl.empty:
        _inc_val_cols = [c for c in _inc_tbl.columns if c != "Type"]
        st.dataframe(
            _inc_tbl.style
                .format({c: "${:,.0f}" for c in _inc_val_cols})
                .map(colour_cell, subset=_inc_val_cols),
            use_container_width=True, hide_index=True,
        )


# ═══ TAB 3 — By Account ═══════════════════════════════════════════════════════
with tab_breakdown:
    def _show_pivot(pv: pd.DataFrame, title: str):
        yr_cols = [c for c in pv.columns if c != "Account"]
        st.subheader(title)
        st.dataframe(style_table(pv, yr_cols), use_container_width=True, hide_index=True)

    _pivots = transaction_tabs.by_account_pivots(
        df,
        all_accounts=all_accounts,
        selected_accounts=accounts,
    )

    _show_pivot(_pivots["net_cash_flow"], "Net Cash Flow by Account")
    st.divider()
    _show_pivot(_pivots["div_rewards"], "Div + Rewards by Account")
    st.divider()
    _show_pivot(_pivots["margin_fees"], "Margin + Fees by Account")

    # ── Crypto Flow ────────────────────────────────────────────────────────────
    _crypto_flow = transaction_tabs.crypto_flow_summary(df)
    if _crypto_flow["has_crypto_flow"]:
        st.divider()
        st.subheader("Crypto Flow — Coinbase (external movements only)")
        col_in, col_out, col_net = st.columns(3)
        with col_in:
            st.markdown("**Inflows**")
            in_df = _crypto_flow["inflows"]
            st.dataframe(in_df.style.format({"Amount": "${:,.2f}"}, na_rep="")
                             .apply(_bold_last_row, last_idx=in_df.index[-1], axis=1),
                         use_container_width=True, hide_index=True)
        with col_out:
            st.markdown("**Outflows**")
            out_df = _crypto_flow["outflows"]
            st.dataframe(out_df.style.format({"Amount": "${:,.2f}"}, na_rep="")
                             .apply(_bold_last_row, last_idx=out_df.index[-1], axis=1),
                         use_container_width=True, hide_index=True)
        with col_net:
            st.markdown("**Net**")
            st.metric("Total In",  f"${_crypto_flow['total_in']:,.2f}")
            st.metric("Total Out", f"${_crypto_flow['total_out']:,.2f}")
            st.metric("Net Cash",  f"${_crypto_flow['net']:,.2f}", delta=f"${_crypto_flow['net']:,.2f}")


# ═══ TAB 4 — Positions ════════════════════════════════════════════════════════
with tab_positions:
    _all_brokers = positions_tab.broker_filter_options(df_all)
    _sel_brokers = st.multiselect("Broker filter", _all_brokers, default=_all_brokers,
                                   key="pos_broker_filter")
    _acct_broker = positions_tab.account_broker_map(df_all)

    def _filter_pos(frame: pd.DataFrame) -> pd.DataFrame:
        return positions_tab.filter_positions_by_broker(frame, _acct_broker, _sel_brokers)

    _ptab_eq, _ptab_opt, _ptab_fut, _ptab_cry = st.tabs(
        ["Equity", "Options", "Futures", "Crypto"]
    )

    # ── Equity sub-tab ─────────────────────────────────────────────────────────
    with _ptab_eq:
        pos_raw = _filter_pos(_load_positions())
        if pos_raw.empty:
            st.info("No equity positions — run the broker MCP/CLI sync first; CSV position files are fallback imports.")
        else:
            _equity_result = positions_tab.equity_positions_summary(pos_raw, df_all)
            sym = _equity_result["holdings"]
            _totals = _equity_result["totals"]
            _pos_cols = positions_tab.EQUITY_COLUMNS
            _money_p  = ["Market_Value", "Total_Cost", "PnL", "Dividends"]
            _colour_p = ["PnL", "Return_%"]
            _fmt_p    = {c: "${:,.0f}" for c in _money_p}
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
            fc1.metric("Market Value", f"${_totals['market_value']:,.0f}")
            fc2.metric("Total Cost",   f"${_totals['total_cost']:,.0f}")
            fc3.metric("P&L",          f"${_totals['pnl']:+,.0f}")
            fc4.metric("Return",       f"{_totals['return_pct']:+.2f}%",
                       delta=f"{_totals['return_pct']:+.2f}%", delta_color="normal")
            fc5.metric("Dividends",    f"${_totals['dividends']:,.0f}")

    # ── Options sub-tab ────────────────────────────────────────────────────────
    with _ptab_opt:
        opt_df = _filter_pos(_load_options())
        if opt_df.empty:
            st.info("No options positions in the database.")
        else:
            _opt_result = positions_tab.option_account_groups(opt_df)

            # Per-account breakdown
            for _group in _opt_result["groups"]:
                with st.expander(_group["label"], expanded=True):
                    _show_cols = _group["show_columns"]
                    _opt_fmt = {}
                    for _fc in ["price", "strike"]:
                        if _fc in _show_cols:
                            _opt_fmt[_fc] = "${:.2f}"
                    if "MARKET VALUE" in _show_cols:
                        _opt_fmt["MARKET VALUE"] = "${:,.0f}"
                    st.dataframe(
                        _group["table"]
                            .style.format(_opt_fmt, na_rep=""),
                        use_container_width=True, hide_index=True,
                    )

            st.markdown("<hr style='margin:4px 0; border-color:#6b7280'>", unsafe_allow_html=True)
            _oc1, _oc2 = st.columns(2)
            _oc1.metric("Total Contracts", _opt_result["total_contracts"])
            _oc2.metric("Total Market Value", f"${_opt_result['total_market_value']:,.0f}")

    # ── Futures sub-tab ────────────────────────────────────────────────────────
    with _ptab_fut:
        fut_df = _filter_pos(_load_futures())
        if fut_df.empty:
            st.info("No futures positions in the database.")
        else:
            _fut_result = positions_tab.futures_account_groups(fut_df)

            for _group in _fut_result["groups"]:
                with st.expander(_group["label"], expanded=True):
                    _show_cols = _group["show_columns"]
                    _fut_fmt = {}
                    if "price" in _show_cols:
                        _fut_fmt["price"] = "${:,.2f}"
                    if "MARKET VALUE" in _show_cols:
                        _fut_fmt["MARKET VALUE"] = "${:+,.0f}"
                    st.dataframe(
                        _group["table"]
                            .style.format(_fut_fmt, na_rep="")
                            .map(colour_cell, subset=["MARKET VALUE"]),
                        use_container_width=True, hide_index=True,
                    )

            st.markdown("<hr style='margin:4px 0; border-color:#6b7280'>", unsafe_allow_html=True)
            _fc1, _fc2 = st.columns(2)
            _fc1.metric("Total Contracts", _fut_result["total_contracts"])
            _fc2.metric("Net Market Value", f"${_fut_result['net_market_value']:+,.0f}")

    # ── Crypto sub-tab ─────────────────────────────────────────────────────────
    with _ptab_cry:
        cry_df = _filter_pos(_load_crypto())
        if cry_df.empty:
            st.info("No crypto positions in the database.")
        else:
            _cry_result = positions_tab.crypto_positions_summary(cry_df)
            _show_cols = _cry_result["show_columns"]
            _cry_fmt = {c: "${:,.4f}" for c in ["price"]} if "price" in _show_cols else {}
            for _mc in ["cost_basis", "MARKET VALUE"]:
                if _mc in _show_cols:
                    _cry_fmt[_mc] = "${:,.0f}"

            st.subheader(f"Crypto — {_cry_result['holding_count']} holdings")
            st.dataframe(
                _cry_result["table"]
                    .style.format(_cry_fmt, na_rep=""),
                use_container_width=True, hide_index=True,
            )
            st.markdown("<hr style='margin:4px 0; border-color:#6b7280'>", unsafe_allow_html=True)
            _cc1, _cc2, _cc3 = st.columns(3)
            _cc1.metric("Market Value", f"${_cry_result['market_value']:,.0f}")
            _cc2.metric("Cost Basis",   f"${_cry_result['cost_basis']:,.0f}")
            _cc3.metric("P&L",          f"${_cry_result['pnl']:+,.0f}")


# ═══ TAB 5 — Transactions ══════════════════════════════════════════════════════
with tab_txns:
    col1, col2, col3, col4 = st.columns([2, 2, 2, 3])
    _txn_options = transaction_tabs.transaction_filter_options(df)
    with col1:
        cat_filter = st.multiselect("Category",
                                    _txn_options["categories"],
                                    default=_txn_options["categories"])
    with col2:
        _txn_brokers = _txn_options["brokers"]
        broker_filter = st.multiselect("Broker", _txn_brokers, default=_txn_brokers)
    with col3:
        yr_filter = st.multiselect("Year",
                                   _txn_options["years"],
                                   default=[])
    with col4:
        search = st.text_input("Search description",
                               placeholder="AAPL, margin, staking …")

    txn = transaction_tabs.filtered_transactions_table(
        df,
        categories=cat_filter,
        brokers=broker_filter,
        years=yr_filter,
        search=search,
    )
    st.caption(f"{len(txn):,} rows")
    st.dataframe(
        txn,
        use_container_width=True,
        column_config={
            "date":   st.column_config.DateColumn("Date", format="YYYY-MM-DD"),
            "amount": st.column_config.NumberColumn("Amount", format="$%.2f"),
        },
    )
    st.download_button("⬇ Download CSV",
                       txn.to_csv(index=False).encode(),
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
        _perf_tables = performance_tab.performance_tables(
            all_positions=_all_pos,
            snapshot_periods=_snap_df,
            cash_balance=_cash_balance,
        )

        # ── Section 1: Portfolio Summary (1-Week) ──────────────────────────────
        st.markdown("##### Portfolio Summary")

        _sum_df = _perf_tables["summary"]
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

        if not _perf_tables["has_snapshots"]:
            st.caption("Historical data accumulates with each `ingest.py` run.")

        st.divider()

        # ── Section 2: Portfolio Returns ───────────────────────────────────────
        st.markdown("##### Portfolio Returns")

        _ret_df   = _perf_tables["returns"]
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


# ═══ TAB 7 — Broker MCP ══════════════════════════════════════════════════════
with tab_brokers:
    st.subheader("Broker MCP")
    st.caption("Health checks prove MCP reachability. Portfolio tables still use the last synced SQLite data.")

    if st.button("Check MCP health", key="broker_health_refresh"):
        _load_mcp_health.clear()
        try:
            st.session_state.broker_mcp_health = _load_mcp_health()
        except Exception as _exc:
            st.error(f"MCP health check failed: {_exc}")
            st.session_state.broker_mcp_health = pd.DataFrame()

    _health = st.session_state.get("broker_mcp_health", pd.DataFrame())

    if _health.empty:
        st.info("Click **Check MCP health** to test configured broker MCPs.")
    else:
        st.dataframe(
            _health,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Status": st.column_config.TextColumn("Status"),
                "Tools": st.column_config.NumberColumn("Tools", format="%d"),
            },
        )
        _bad = _health[~_health["Status"].isin(["OK"])]
        if not _bad.empty:
            st.warning("Some broker MCPs are not healthy; synced DB balances may be stale for those accounts.")

    st.divider()
    st.markdown("##### CLI module review")
    _cli_review = pd.DataFrame([
        {
            "Broker": "Coinbase",
            "Dashboard use": "Safe on button click",
            "Source": "src.cli.coinbase.fetch_balances",
            "Note": "Loads credentials from coinbase-derivatives-mcp config.",
        },
        {
            "Broker": "Tradier",
            "Dashboard use": "Safe on button click when token env is set",
            "Source": "src.cli.tradier.fetch_balances/fetch_positions",
            "Note": "No hard-coded token; requires TRADIER_ACCESS_TOKEN or TRADIER_MCP_BEARER_TOKEN.",
        },
        {
            "Broker": "Robinhood",
            "Dashboard use": "Needs auth/token cleanup before embedding",
            "Source": "src.cli.robinhood",
            "Note": "Interactive OAuth and profile prompts should stay CLI-only for now.",
        },
        {
            "Broker": "Webull / Schwab / TradeStation / Fidelity",
            "Dashboard use": "Prefer DB/cached JSON views",
            "Source": "src.cli.* loaders",
            "Note": "Modules are terminal-first; convert fetch/load functions before adding live buttons.",
        },
    ])
    st.dataframe(_cli_review, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("##### Live checks")
    st.caption("These actions call broker APIs only when clicked.")

    _live_cols = st.columns(2)
    with _live_cols[0]:
        if st.button("Fetch Coinbase balances", key="live_coinbase_balances"):
            try:
                from src.cli.coinbase import fetch_balances as _fetch_coinbase_balances

                _rows = _fetch_coinbase_balances()
                _cb_df = pd.DataFrame(_rows)
                _cols = [c for c in ["asset", "total", "price_usd", "usd_value", "venue"] if c in _cb_df.columns]
                if _cb_df.empty:
                    st.info("Coinbase returned no balances.")
                else:
                    st.dataframe(_cb_df[_cols], use_container_width=True, hide_index=True)
            except Exception as _exc:
                st.error(f"Coinbase live fetch failed: {_exc}")

    with _live_cols[1]:
        if st.button("Fetch Tradier balances", key="live_tradier_balances"):
            try:
                from src.cli.tradier import fetch_balances as _fetch_tradier_balances

                _bal = _fetch_tradier_balances()
                st.json(_bal)
            except Exception as _exc:
                st.error(f"Tradier live fetch failed: {_exc}")


# ═══ TAB 8 — Settings ════════════════════════════════════════════════════════
with tab_settings:
    st.subheader("Settings")
    st.caption("Changes take effect immediately after Save. Margin/price source are used on next sync.")

    # Accounts whose margin comes from the broker API — shown read-only
    _API_MARGIN_ACCOUNTS = {"RH-BV", "WEBULL", "WEBULL-CASH", "WEBULL-EVENTS",
                            "WEBULL-FUT", "TS", "SCHWAB"}
    # Accounts that can have a futures equity override
    _FUTURES_EQUITY_ACCOUNTS = {"SCHWAB"}

    _acct_df = load_account_settings()

    # ── Section 1: Cash Balance ───────────────────────────────────────────────
    st.markdown("##### Cash Balance")
    st.caption("Combined balance across Fidelity CMA, PNC, Huntington, Clearview.")
    _cur_cash = get_cash_balance()
    _new_cash = st.number_input(
        "Cash & Savings ($)",
        min_value=0.0,
        value=float(_cur_cash),
        step=100.0,
        format="%.2f",
        key="settings_cash",
    )

    st.divider()

    # ── Section 2: Account Settings ───────────────────────────────────────────
    st.markdown("##### Account Settings")
    st.caption("Active and price source are applied immediately. "
               "Margin override is used as fallback when the broker API does not return margin data. "
               "Cost basis adjustment is added to broker-reported account cost basis.")

    if _acct_df.empty:
        st.info("No accounts found. Run `python ingest.py` first.")
    else:
        _edited_rows = []
        for _, _row in _acct_df.iterrows():
            _acct = str(_row["account_id"])
            with st.expander(_acct, expanded=False):
                _c1, _c2, _c3 = st.columns([1, 1, 2])

                _active = _c1.checkbox(
                    "Active",
                    value=bool(_row.get("active", 1)),
                    key=f"active_{_acct}",
                )
                _price_src = _c2.selectbox(
                    "Price Source",
                    options=["live", "static"],
                    index=0 if str(_row.get("price_source", "live")) == "live" else 1,
                    key=f"price_src_{_acct}",
                )

                _has_api_margin = _acct in _API_MARGIN_ACCOUNTS
                _cur_margin_override = _row.get("margin_override")
                _cur_margin_override = float(_cur_margin_override) if pd.notna(_cur_margin_override) else None

                if _has_api_margin:
                    _c3.text_input(
                        "Margin (from API — read only)",
                        value="Set automatically during sync",
                        disabled=True,
                        key=f"margin_ro_{_acct}",
                    )
                    _margin_override = None
                else:
                    _margin_override = _c3.number_input(
                        "Margin Override ($)",
                        min_value=0.0,
                        value=float(_cur_margin_override) if _cur_margin_override is not None else 0.0,
                        step=100.0,
                        format="%.2f",
                        key=f"margin_{_acct}",
                        help="Set to 0 to clear the override.",
                    )
                    _margin_override = _margin_override if _margin_override > 0 else None

                _futures_override = None
                if _acct in _FUTURES_EQUITY_ACCOUNTS:
                    st.markdown("**Futures Sub-Account Equity**")
                    st.caption("Schwab Futures Account Value from the Schwab balance page. "
                               "Used because the futures sub-account is not returned by the API.")
                    _cur_fe = _row.get("futures_equity_override")
                    _cur_fe = float(_cur_fe) if pd.notna(_cur_fe) else 0.0
                    _futures_override = st.number_input(
                        "Futures Equity ($)",
                        min_value=0.0,
                        value=_cur_fe,
                        step=100.0,
                        format="%.2f",
                        key=f"futures_eq_{_acct}",
                        help="Set to 0 to clear.",
                    )
                    _futures_override = _futures_override if _futures_override > 0 else None

                _cost_basis_adjustment = 0.0
                if _acct == "COINBASE":
                    _cur_cb_adj = _row.get("cost_basis_adjustment")
                    _cur_cb_adj = float(_cur_cb_adj) if pd.notna(_cur_cb_adj) else 0.0
                    st.markdown("**Coinbase MCP Cost Basis Adjustment**")
                    st.caption("Added to Coinbase MCP cost basis. Use a negative value to subtract.")
                    _cost_basis_adjustment = st.number_input(
                        "Cost Basis Adjustment ($)",
                        value=_cur_cb_adj,
                        step=100.0,
                        format="%.2f",
                        key=f"cost_basis_adj_{_acct}",
                    )

                _edited_rows.append({
                    "account_id":              _acct,
                    "active":                  int(_active),
                    "price_source":            _price_src,
                    "margin_override":         _margin_override,
                    "futures_equity_override": _futures_override,
                    "cost_basis_adjustment":   _cost_basis_adjustment,
                })

    st.divider()

    # ── Save All ──────────────────────────────────────────────────────────────
    if st.button("💾 Save All", type="primary"):
        try:
            # Cash balance
            if _new_cash != _cur_cash:
                upsert_cash_balance(_new_cash)

            # Account settings
            if not _acct_df.empty:
                save_account_settings(_edited_rows)

            # Immediately apply futures equity overrides — write _FUTURES_ADJ_ row
            # so the dashboard reflects the new value without requiring a full sync.
            from src.db import get_conn, insert_futures, delete_futures_by_account
            for _r in _edited_rows:
                if _r["account_id"] in _FUTURES_EQUITY_ACCOUNTS:
                    _fe = _r.get("futures_equity_override")
                    # Remove any existing adj row first
                    with get_conn() as _conn:
                        _conn.execute(
                            "DELETE FROM futures_positions WHERE account_id=? AND symbol='_FUTURES_ADJ_'",
                            (_r["account_id"],),
                        )
                        _conn.commit()
                    # Write new adj row if value > 0
                    if _fe and _fe > 0:
                        insert_futures([{
                            "account_id":   _r["account_id"],
                            "symbol":       "_FUTURES_ADJ_",
                            "underlying":   None,
                            "description":  "Futures account equity adjustment",
                            "qty":          0,
                            "price":        None,
                            "market_value": _fe,
                            "data_source":  "manual",
                            "source_file":  None,
                        }])

            st.cache_data.clear()
            st.success("Settings saved.")
            st.rerun()
        except Exception as _exc:
            st.error(f"Save failed: {_exc}")
