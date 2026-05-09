"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  BarChart3,
  BriefcaseBusiness,
  ClipboardList,
  DatabaseZap,
  Landmark,
  LineChart,
  Settings,
  Table2
} from "lucide-react";

type ApiReceipt<T> = {
  status: string;
  operation: string;
  generated_at: string;
  warnings: string[];
  errors: string[];
  data?: T;
  count?: number;
};

type PortfolioSummary = {
  label: string;
  net_cash_flow: number;
  dividends: number;
  rewards: number;
  margin_interest: number;
  fees: number;
  net_income: number;
  transaction_count: number;
  date_range: string;
  live_net_worth?: number;
  live_market_value?: number;
  live_margin?: number;
};

type PositionsPayload = {
  summary: Record<string, unknown> | null;
  canonical_positions: Array<Record<string, unknown>>;
};

type DashboardPortfolioPayload = {
  net_worth: {
    net_worth: number;
    market_value: number;
    margin: number;
    source: string;
  };
  transaction_kpis: Record<string, number>;
  account_summary: Array<Record<string, unknown>>;
  asset_class_breakdown: Array<Record<string, unknown>>;
  futures_by_commodity: Array<Record<string, unknown>>;
  sector_allocation: Array<Record<string, unknown>>;
  positions_by_account: Array<Record<string, unknown>>;
  sector_summary: Array<Record<string, unknown>>;
};

type DashboardPerformancePayload = {
  summary: Array<Record<string, unknown>>;
  returns: Array<Record<string, unknown>>;
  has_snapshots: boolean;
};

type DashboardYearlyPayload = {
  summary: Array<Record<string, unknown>>;
  income_breakdown: Array<Record<string, unknown>>;
};

type DashboardByAccountPayload = {
  net_cash_flow: Array<Record<string, unknown>>;
  div_rewards: Array<Record<string, unknown>>;
  margin_fees: Array<Record<string, unknown>>;
  crypto_flow: {
    has_crypto_flow: boolean;
    total_in: number;
    total_out: number;
    net: number;
    inflows: Array<Record<string, unknown>>;
    outflows: Array<Record<string, unknown>>;
  };
};

type TransactionsPayload = {
  count: number;
  transactions: Array<Record<string, unknown>>;
};

type MetricsRow = {
  label: string;
  broker?: string;
  net_cash_flow: number;
  dividends: number;
  rewards: number;
  margin_interest: number;
  fees: number;
  net_income: number;
};

type CapabilityPayload = {
  tabs: string[];
  capability_count: number;
  capability_counts: Record<string, number>;
  capabilities: Array<{
    tab: string;
    name: string;
    data_sources: string[];
  }>;
};

type OperationsStatusPayload = {
  accounts: Array<Record<string, unknown>>;
  health: Array<Record<string, unknown>>;
  csv_uploads?: Record<string, unknown>;
  csv_ingest_state?: Array<Record<string, unknown>>;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

const tabs = [
  { id: "portfolio", label: "Portfolio", icon: BriefcaseBusiness },
  { id: "yearly", label: "Yearly Summary", icon: BarChart3 },
  { id: "account", label: "By Account", icon: Landmark },
  { id: "positions", label: "Positions", icon: Table2 },
  { id: "transactions", label: "Transactions", icon: ClipboardList },
  { id: "performance", label: "Performance", icon: LineChart },
  { id: "broker", label: "Health Checks", icon: DatabaseZap },
  { id: "settings", label: "Settings", icon: Settings }
] as const;

type TabId = (typeof tabs)[number]["id"];

const positionTabs = [
  { id: "equity", label: "Equity", columns: ["account_id", "symbol", "name", "quantity", "price", "cost_basis", "market_value", "unrealized_pnl", "sector"] },
  { id: "options", label: "Options", columns: ["account_id", "symbol", "underlying", "expiration", "strike", "call_put", "quantity", "price", "market_value"] },
  { id: "futures", label: "Futures", columns: ["account_id", "symbol", "name", "quantity", "price", "market_value"] },
  { id: "crypto", label: "Crypto", columns: ["account_id", "symbol", "name", "quantity", "price", "cost_basis", "market_value", "unrealized_pnl"] }
] as const;

type PositionTabId = (typeof positionTabs)[number]["id"];

const MONEY_KEYWORDS = [
  "value", "cost", "basis", "margin", "equity", "pnl", "p&l",
  "income", "dividend", "reward", "fee", "interest", "worth",
  "amount", "cash", "flow", "mv", "alloc", "return", "change",
  "price", "net_income", "net_cash", "net_equity", "market_value",
  "cost_basis", "unrealized_pnl", "total_cost", "dividends",
];

const PCT_KEYWORDS = ["alloc_%", "return_%", "% change", "1-week", "1-month", "3-month", "ytd", "1-year"];

function isMoneyColumn(col: string): boolean {
  const lower = col.toLowerCase();
  if (PCT_KEYWORDS.some((k) => lower.includes(k))) return false;
  return MONEY_KEYWORDS.some((k) => lower.includes(k));
}

function isPctColumn(col: string): boolean {
  return PCT_KEYWORDS.some((k) => col.toLowerCase().includes(k));
}

function isNumericColumn(col: string): boolean {
  const lower = col.toLowerCase();
  return isMoneyColumn(col) || isPctColumn(col) || ["quantity", "shares", "contracts", "strike", "txns"].some((k) => lower.includes(k));
}

function currency(value: unknown) {
  const number = Number(value ?? 0);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0
  }).format(Number.isFinite(number) ? number : 0);
}

function currencyFine(value: unknown) {
  const number = Number(value ?? 0);
  if (!Number.isFinite(number)) return "$0.00";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  }).format(number);
}

function pct(value: unknown) {
  const number = Number(value ?? 0);
  if (!Number.isFinite(number)) return "—";
  return (number >= 0 ? "+" : "") + number.toFixed(2) + "%";
}

async function getJson<T>(path: string): Promise<ApiReceipt<T>> {
  const response = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

function DataTable({
  rows,
  columns,
  capped = false,
  totalRowLabel = "TOTAL",
  loading = false,
}: {
  rows: Array<Record<string, unknown>>;
  columns: string[];
  capped?: boolean;
  totalRowLabel?: string;
  loading?: boolean;
}) {
  const [sortColumn, setSortColumn] = useState<string | null>(null);
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("asc");

  const sortedRows = useMemo(() => {
    if (!sortColumn) return rows;
    const sorted = [...rows];
    sorted.sort((a, b) => compareValues(a[sortColumn], b[sortColumn], sortColumn, sortDirection));
    return sorted;
  }, [rows, sortColumn, sortDirection]);

  if (loading) {
    return <div className="empty">Loading…</div>;
  }
  if (!rows.length) {
    return <div className="empty">No rows</div>;
  }

  function onSort(col: string) {
    if (sortColumn !== col) {
      setSortColumn(col);
      setSortDirection("asc");
      return;
    }
    setSortDirection((prev) => (prev === "asc" ? "desc" : "asc"));
  }

  return (
    <div className={`tableWrap${capped ? " capped" : ""}`}>
      <table>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col} className={isNumericColumn(col) ? "num" : ""}>
                <button type="button" className="sortBtn" onClick={() => onSort(col)}>
                  <span>{prettyHeader(col)}</span>
                  <span className="sortGlyph" aria-hidden="true">
                    {sortColumn === col ? (sortDirection === "asc" ? "▲" : "▼") : "↕"}
                  </span>
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row, rowIndex) => (
            <tr key={rowIndex} className={isTotalRow(row, columns, totalRowLabel) ? "footer-row" : ""}>
              {columns.map((col) => {
                const raw = row[col];
                const num = Number(raw);
                const isMoney = isMoneyColumn(col);
                const isPct = isPctColumn(col);
                const isNum = typeof raw === "number" || (typeof raw === "string" && raw !== "" && !isNaN(num));
                const negative = isNum && num < 0;

                let display: string;
                if (raw === null || raw === undefined || raw === "") {
                  display = "—";
                } else if (isMoney && isNum) {
                  display = currencyFine(raw);
                } else if (isPct && isNum) {
                  display = pct(raw);
                } else {
                  display = formatCell(raw);
                }
                return (
                  <td
                    key={col}
                    className={[
                      isNum ? "num" : "",
                      negative ? "neg" : (isNum && num > 0 && (isMoney || isPct) ? "pos" : ""),
                    ].join(" ").trim() || undefined}
                  >
                    {display}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatTimestamp(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });
}

function isTotalRow(row: Record<string, unknown>, columns: string[], totalRowLabel?: string) {
  const firstColumn = columns[0];
  const value = String(row[firstColumn] ?? "").trim().toUpperCase();
  const target = (totalRowLabel ?? "TOTAL").toUpperCase();
  return value === target;
}

function compareValues(left: unknown, right: unknown, column: string, direction: "asc" | "desc") {
  const leftMissing = left === null || left === undefined || left === "";
  const rightMissing = right === null || right === undefined || right === "";
  if (leftMissing && rightMissing) return 0;
  if (leftMissing) return 1;
  if (rightMissing) return -1;

  let comparison = 0;
  if (isNumericColumn(column)) {
    const leftNumber = Number(left);
    const rightNumber = Number(right);
    const leftValid = Number.isFinite(leftNumber);
    const rightValid = Number.isFinite(rightNumber);
    if (leftValid && rightValid) {
      comparison = leftNumber - rightNumber;
    } else {
      comparison = String(left).localeCompare(String(right), undefined, { sensitivity: "base" });
    }
  } else {
    comparison = String(left).localeCompare(String(right), undefined, { sensitivity: "base" });
  }

  return direction === "asc" ? comparison : -comparison;
}

function formatCell(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "—";
    return Number.isInteger(value) ? value.toLocaleString() : value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  if (typeof value === "string") {
    const lower = value.toLowerCase();
    if (lower.includes("t") && (lower.endsWith("z") || lower.includes("+00:00"))) {
      return formatTimestamp(value);
    }
  }
  return String(value);
}

const HEADER_MAP: Record<string, string> = {
  account_id: "Account",
  symbol: "Symbol",
  name: "Name",
  quantity: "Qty",
  price: "Price",
  cost_basis: "Cost Basis",
  market_value: "Market Value",
  unrealized_pnl: "Unrealized P/L",
  sector: "Sector",
  underlying: "Underlying",
  expiration: "Expiration",
  strike: "Strike",
  call_put: "Type",
  totalReturn: "Total Return",
  net_cash_flow: "Net Cash Flow",
  dividends: "Dividends",
  rewards: "Rewards",
  margin_interest: "Margin Interest",
  fees: "Fees",
  net_income: "Net Income",
  description: "Description",
  category: "Category",
  broker: "Broker",
  date: "Date",
  amount: "Amount",
  "Alloc_%": "Alloc %",
  "Return_%": "Return %",
  "PnL": "P/L",
  "Market_Value": "Market Value",
  "Total_Cost": "Total Cost",
  "Net_MV": "Net MV",
};

function prettyHeader(col: string): string {
  if (HEADER_MAP[col]) return HEADER_MAP[col];
  return col
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export default function Home() {
  const [activeTab, setActiveTab] = useState<TabId>("portfolio");
  const [activePositionTab, setActivePositionTab] = useState<PositionTabId>("equity");
  const [summary, setSummary] = useState<ApiReceipt<PortfolioSummary> | null>(null);
  const [positions, setPositions] = useState<ApiReceipt<PositionsPayload> | null>(null);
  const [dashboardPortfolio, setDashboardPortfolio] = useState<ApiReceipt<DashboardPortfolioPayload> | null>(null);
  const [dashboardPerformance, setDashboardPerformance] = useState<ApiReceipt<DashboardPerformancePayload> | null>(null);
  const [dashboardYearly, setDashboardYearly] = useState<ApiReceipt<DashboardYearlyPayload> | null>(null);
  const [dashboardByAccount, setDashboardByAccount] = useState<ApiReceipt<DashboardByAccountPayload> | null>(null);
  const [transactions, setTransactions] = useState<ApiReceipt<TransactionsPayload> | null>(null);
  const [yearlySummary, setYearlySummary] = useState<ApiReceipt<MetricsRow[]> | null>(null);
  const [accountSummary, setAccountSummary] = useState<ApiReceipt<MetricsRow[]> | null>(null);
  const [capabilities, setCapabilities] = useState<ApiReceipt<CapabilityPayload> | null>(null);
  const [operations, setOperations] = useState<ApiReceipt<OperationsStatusPayload> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [positionBrokerFilter, setPositionBrokerFilter] = useState<string>("ALL");
  const [txCategory, setTxCategory] = useState<string>("ALL");
  const [txBroker, setTxBroker] = useState<string>("ALL");
  const [txYear, setTxYear] = useState<string>("ALL");
  const [txSearch, setTxSearch] = useState<string>("");
  const [dateFrom, setDateFrom] = useState<string>("");
  const [dateTo, setDateTo] = useState<string>("");
  const [includeTransfers, setIncludeTransfers] = useState<boolean>(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const [
          summaryData,
          positionsData,
          dashboardPortfolioData,
          dashboardPerformanceData,
          dashboardYearlyData,
          dashboardByAccountData,
          transactionData,
          yearlySummaryData,
          accountSummaryData,
          capabilityData,
          operationsDataResult
        ] = await Promise.allSettled([
          getJson<PortfolioSummary>("/portfolio/summary?include_live_net_worth=false"),
          getJson<PositionsPayload>("/portfolio/positions"),
          getJson<DashboardPortfolioPayload>("/dashboard/portfolio"),
          getJson<DashboardPerformancePayload>("/dashboard/performance"),
          getJson<DashboardYearlyPayload>("/dashboard/yearly-summary"),
          getJson<DashboardByAccountPayload>("/dashboard/by-account"),
          getJson<TransactionsPayload>("/transactions?limit=25"),
          getJson<MetricsRow[]>("/portfolio/yearly-summary"),
          getJson<MetricsRow[]>("/portfolio/account-summary"),
          getJson<CapabilityPayload>("/dashboard/capabilities"),
          getJson<OperationsStatusPayload>("/operations/status")
        ]);
        const required = [
          summaryData,
          positionsData,
          dashboardPortfolioData,
          dashboardPerformanceData,
          dashboardYearlyData,
          dashboardByAccountData,
          transactionData,
          yearlySummaryData,
          accountSummaryData,
          capabilityData,
        ];
        const failedRequired = required.find((result) => result.status === "rejected");
        if (failedRequired) {
          throw (failedRequired as PromiseRejectedResult).reason;
        }
        if (!cancelled) {
          setSummary((summaryData as PromiseFulfilledResult<ApiReceipt<PortfolioSummary>>).value);
          setPositions((positionsData as PromiseFulfilledResult<ApiReceipt<PositionsPayload>>).value);
          setDashboardPortfolio((dashboardPortfolioData as PromiseFulfilledResult<ApiReceipt<DashboardPortfolioPayload>>).value);
          setDashboardPerformance((dashboardPerformanceData as PromiseFulfilledResult<ApiReceipt<DashboardPerformancePayload>>).value);
          setDashboardYearly((dashboardYearlyData as PromiseFulfilledResult<ApiReceipt<DashboardYearlyPayload>>).value);
          setDashboardByAccount((dashboardByAccountData as PromiseFulfilledResult<ApiReceipt<DashboardByAccountPayload>>).value);
          setTransactions((transactionData as PromiseFulfilledResult<ApiReceipt<TransactionsPayload>>).value);
          setYearlySummary((yearlySummaryData as PromiseFulfilledResult<ApiReceipt<MetricsRow[]>>).value);
          setAccountSummary((accountSummaryData as PromiseFulfilledResult<ApiReceipt<MetricsRow[]>>).value);
          setCapabilities((capabilityData as PromiseFulfilledResult<ApiReceipt<CapabilityPayload>>).value);
          if (operationsDataResult.status === "fulfilled") {
            setOperations(operationsDataResult.value);
            setError(null);
          } else {
            setOperations(null);
            setError(null);
          }
          setLoading(false);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Failed to load API data");
          setLoading(false);
        }
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const positionRows = positions?.data?.canonical_positions ?? [];
  const operationAccountRows = useMemo(() => {
    const rows = operations?.data?.accounts;
    if (Array.isArray(rows) && rows.length) return rows;
    const fromSummary = dashboardPortfolio?.data?.account_summary ?? [];
    const forcedStaleAccounts = new Set(["RH-KD", "FIDELITY"]);
    return fromSummary.map((row) => {
      const accountId = String(row.Account ?? row.account_id ?? "UNKNOWN").toUpperCase();
      const snapshotRaw = summary?.generated_at ?? null;
      const isForcedStale = forcedStaleAccounts.has(accountId);
      return ({
      account_id: row.Account ?? row.account_id ?? "UNKNOWN",
      broker: row.Broker ?? row.broker ?? "UNKNOWN",
      account_type: "portfolio",
      active: 1,
      source_signal: "DB",
      last_synced_ts: summary?.generated_at ?? null,
      last_snapshot_date: summary?.generated_at ?? null,
      age_hours: null,
      status_label: isForcedStale ? "STALE" : "OK",
    });
    });
  }, [operations, dashboardPortfolio, summary]);
  const operationHealthRows = useMemo(() => {
    const rows = operations?.data?.health;
    if (Array.isArray(rows) && rows.length) {
      const out = [...rows];
      const fidTs = operations?.data?.csv_uploads?.fidelity_last_upload_ts;
      if (fidTs) {
        out.push({
          Broker: "Fidelity CSV",
          Accounts: "FIDELITY",
          Status: "INFO",
          Tools: "—",
          Detail: `Last upload: ${formatTimestamp(fidTs)}`,
        });
      }
      return out;
    }
    return [
      {
        Broker: "MCP API",
        Accounts: 0,
        Status: "UNAVAILABLE",
        Tools: 0,
        Detail: "Route /operations/status not available on current API server build",
      },
    ];
  }, [operations]);
  const csvSyncRows = useMemo(() => operations?.data?.csv_ingest_state ?? [], [operations]);
  const transactionRows = transactions?.data?.transactions ?? [];
  const performanceSummaryRows = dashboardPerformance?.data?.summary ?? [];
  const performanceReturnRows = dashboardPerformance?.data?.returns ?? [];
  const yearlyRows = yearlySummary?.data ?? [];
  const accountRows = accountSummary?.data ?? [];
  const yearlyDashboardRows = dashboardYearly?.data?.summary ?? [];
  const incomeBreakdownRows = dashboardYearly?.data?.income_breakdown ?? [];
  const byAccountData = dashboardByAccount?.data;
  const assetRows = useMemo(() => {
    const rows = positions?.data?.summary?.by_asset_class;
    return Array.isArray(rows) ? (rows as Array<Record<string, unknown>>) : [];
  }, [positions]);
  const activePositionConfig = positionTabs.find((tab) => tab.id === activePositionTab) ?? positionTabs[0];
  const filteredPositionRows = useMemo(() => {
    return positionRows.filter((row) => {
      const assetMatch = String(row.asset_class ?? "").toLowerCase() === activePositionTab;
      const brokerValue = String(row.broker ?? row.account_id ?? "");
      const brokerMatch = positionBrokerFilter === "ALL" || brokerValue === positionBrokerFilter;
      return assetMatch && brokerMatch;
    });
  }, [positionRows, activePositionTab, positionBrokerFilter]);
  const displayPositionRows = useMemo(() => {
    if (positionBrokerFilter !== "ALL") return filteredPositionRows;
    return consolidatePositionsBySymbol(filteredPositionRows, activePositionTab);
  }, [filteredPositionRows, positionBrokerFilter, activePositionTab]);
  const normalizedDisplayRows = useMemo(() => displayPositionRows.map(normalizeCoinbaseSector), [displayPositionRows]);
  const displayRowsForTable = useMemo(() => {
    if (activePositionTab !== "futures") return normalizedDisplayRows;
    return normalizedDisplayRows.filter((row) => String(row.symbol ?? "") !== "_FUTURES_ADJ_");
  }, [activePositionTab, normalizedDisplayRows]);
  const hasOnlyFuturesAdjustment = useMemo(() => {
    if (activePositionTab !== "futures") return false;
    return normalizedDisplayRows.length > 0 && displayRowsForTable.length === 0;
  }, [activePositionTab, normalizedDisplayRows, displayRowsForTable]);
  const transactionFilteredRows = useMemo(() => {
    return transactionRows.filter((row) => {
      const date = String(row.date ?? "");
      const category = String(row.category ?? "");
      const broker = String(row.broker ?? "");
      const description = String(row.description ?? "");
      const symbol = String(row.symbol ?? "");
      const text = `${description} ${symbol}`.toLowerCase();
      const year = date.length >= 4 ? date.slice(0, 4) : "";
      const fromMatch = !dateFrom || date >= dateFrom;
      const toMatch = !dateTo || date <= dateTo;
      const categoryMatch = txCategory === "ALL" || category === txCategory;
      const brokerMatch = txBroker === "ALL" || broker === txBroker;
      const yearMatch = txYear === "ALL" || year === txYear;
      const searchMatch = !txSearch.trim() || text.includes(txSearch.trim().toLowerCase());
      const transferMatch = includeTransfers || category.toLowerCase() !== "internal transfer";
      return fromMatch && toMatch && categoryMatch && brokerMatch && yearMatch && searchMatch && transferMatch;
    });
  }, [transactionRows, dateFrom, dateTo, txCategory, txBroker, txYear, txSearch, includeTransfers]);
  const txCategories = uniqueValues(transactionRows, "category");
  const txBrokers = uniqueValues(transactionRows, "broker");
  const txYears = Array.from(new Set(transactionRows.map((row) => String(row.date ?? "").slice(0, 4)).filter(Boolean))).sort().reverse();
  const positionBrokerOptions = Array.from(new Set(positionRows.map((row) => String(row.broker ?? row.account_id ?? "")).filter(Boolean))).sort();
  const sectorRows = dashboardPortfolio?.data?.sector_allocation ?? [];
  const filteredSectorSummaryRows = useMemo(() => {
    if (activePositionTab === "equity") return buildSectorSummary(normalizedDisplayRows);
    if (positionBrokerFilter === "COINBASE" && (activePositionTab === "crypto" || activePositionTab === "futures")) {
      return buildSectorSummary(normalizedDisplayRows);
    }
    return [];
  }, [activePositionTab, positionBrokerFilter, normalizedDisplayRows]);
  const futuresCommodityRows = useMemo(
    () => (activePositionTab === "futures" ? buildFuturesCommodityRows(normalizedDisplayRows) : []),
    [activePositionTab, normalizedDisplayRows]
  );

  return (
    <main>
      <aside>
        <div className="brand">
          <Activity size={22} />
          <div>
            <h1>Trading Journal</h1>
            <p>{summary?.data?.date_range ?? "API-backed dashboard"}</p>
          </div>
        </div>
        <nav>
          {tabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                className={activeTab === tab.id ? "active" : ""}
                onClick={() => setActiveTab(tab.id)}
                type="button"
              >
                <Icon size={17} />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </nav>
      </aside>

      <section className="content">
        <header>
          <div>
            <h2>{tabs.find((tab) => tab.id === activeTab)?.label}</h2>
            <p>{capabilities?.data?.capability_count ?? 0} tracked capabilities</p>
          </div>
          <div className={`pill ${error ? "bad" : "good"}`}>{error ? "API offline" : "API online"}</div>
        </header>
        <div className="globalControls">
          <label>Date From<input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} /></label>
          <label>Date To<input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} /></label>
          <label className="checkbox"><input type="checkbox" checked={includeTransfers} onChange={(e) => setIncludeTransfers(e.target.checked)} />Include internal transfers</label>
          <button type="button" onClick={() => window.location.reload()}>Refresh</button>
        </div>

        {error ? <div className="alert">{error}</div> : null}

        {activeTab === "portfolio" ? (
          <div className="stack">
            <div className="metricsGrid">
              <Metric label="Net Worth" value={currency(dashboardPortfolio?.data?.net_worth?.net_worth)} />
              <Metric label="Market Value" value={currency(dashboardPortfolio?.data?.net_worth?.market_value)} />
              <Metric label="Margin Borrowed" value={currency(dashboardPortfolio?.data?.net_worth?.margin)} />
              <Metric label="Net Income" value={currency(summary?.data?.net_income)} />
            </div>
            <Panel title="Transaction KPIs">
              <KpiRow data={dashboardPortfolio?.data?.transaction_kpis} />
            </Panel>
            <Panel title="Account Summary">
                <DataTable
                rows={dashboardPortfolio?.data?.account_summary ?? []}
                columns={["Account", "Broker", "Market Value", "Cost Basis", "Margin", "Net Equity"]}
                loading={loading}
              />
            </Panel>
            <Panel title="Asset Class Breakdown">
                <DataTable
                rows={withMarginAssetClass(dashboardPortfolio?.data?.asset_class_breakdown ?? assetRows, dashboardPortfolio?.data?.net_worth?.margin)}
                columns={["Asset Class", "Market Value", "Allocation"]}
                loading={loading}
              />
            </Panel>
            <Panel title="Sector Allocation">
              <SectorPie rows={sectorRows} />
              <DataTable rows={sectorRows} columns={["sector", "MARKET VALUE"]} loading={loading} />
            </Panel>
          </div>
        ) : null}

        {activeTab === "yearly" ? (
          <div className="stack">
            <Panel title="Year-over-Year Summary">
                <DataTable rows={yearlyDashboardRows} columns={columnsFromRows(yearlyDashboardRows, ["Metric", "ALL"])} loading={loading} />
            </Panel>
            <Panel title="Income Breakdown by Type">
              <DataTable rows={incomeBreakdownRows} columns={columnsFromRows(incomeBreakdownRows, ["Type", "ALL"])} loading={loading} />
            </Panel>
            <Panel title={`${yearlyRows.length.toLocaleString()} Canonical Year Rows`}>
              <DataTable
                rows={yearlyRows}
                columns={["label", "net_cash_flow", "dividends", "rewards", "margin_interest", "fees", "net_income"]}
                loading={loading}
              />
            </Panel>
          </div>
        ) : null}

        {activeTab === "account" ? (
          <div className="stack">
            <Panel title="Net Cash Flow by Account">
              <DataTable rows={byAccountData?.net_cash_flow ?? []} columns={columnsFromRows(byAccountData?.net_cash_flow ?? [], ["Account", "ALL"])} loading={loading} />
            </Panel>
            <Panel title="Div + Rewards by Account">
              <DataTable rows={byAccountData?.div_rewards ?? []} columns={columnsFromRows(byAccountData?.div_rewards ?? [], ["Account", "ALL"])} loading={loading} />
            </Panel>
            <Panel title="Margin + Fees by Account">
              <DataTable rows={byAccountData?.margin_fees ?? []} columns={columnsFromRows(byAccountData?.margin_fees ?? [], ["Account", "ALL"])} loading={loading} />
            </Panel>
            <div className="metricsGrid compact">
              <Metric label="Crypto Total In" value={currency(byAccountData?.crypto_flow.total_in)} />
              <Metric label="Crypto Total Out" value={currency(byAccountData?.crypto_flow.total_out)} />
              <Metric label="Crypto Net Cash" value={currency(byAccountData?.crypto_flow.net)} />
              <Metric label="Accounts" value={accountRows.length.toLocaleString()} />
            </div>
            <Panel title="Crypto Flow Inflows">
              <DataTable rows={byAccountData?.crypto_flow.inflows ?? []} columns={["Type", "Amount", "Txns"]} loading={loading} />
            </Panel>
            <Panel title="Crypto Flow Outflows">
              <DataTable rows={byAccountData?.crypto_flow.outflows ?? []} columns={["Type", "Amount", "Txns"]} loading={loading} />
            </Panel>
          </div>
        ) : null}

        {activeTab === "positions" ? (
          <div className="stack">
            <div className="segmented" role="tablist" aria-label="Position asset classes">
              {positionTabs.map((tab) => (
                <button
                  key={tab.id}
                  className={activePositionTab === tab.id ? "selected" : ""}
                  onClick={() => setActivePositionTab(tab.id)}
                  type="button"
                  role="tab"
                  aria-selected={activePositionTab === tab.id}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <div className="toolbar">
              <label>Broker
                <select value={positionBrokerFilter} onChange={(e) => setPositionBrokerFilter(e.target.value)}>
                  <option value="ALL">All</option>
                  {positionBrokerOptions.map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </label>
            </div>
            <div className="metricsGrid compact">
              <Metric label="All Positions" value={positionRows.length.toLocaleString()} />
              <Metric label={`${activePositionConfig.label} Rows`} value={displayRowsForTable.length.toLocaleString()} />
              <Metric label="Market Value" value={currency(sumRows(normalizedDisplayRows, "market_value"))} />
              <Metric label="Unrealized P/L" value={currency(sumRows(normalizedDisplayRows, "unrealized_pnl"))} />
            </div>
            {(activePositionTab === "options" || activePositionTab === "futures") && positionBrokerFilter !== "ALL" ? (
              groupByAccount(displayRowsForTable).map(([account, rows]) => (
                <details key={account} open>
                  <summary>{account} ({rows.length})</summary>
                  <Panel title={`${activePositionConfig.label} - ${account}`}>
                    <DataTable rows={rows} columns={[...activePositionConfig.columns]} capped loading={loading} />
                  </Panel>
                </details>
              ))
            ) : (
              <Panel title={`${displayRowsForTable.length.toLocaleString()} ${activePositionConfig.label} Positions`}>
                <DataTable rows={displayRowsForTable} columns={[...activePositionConfig.columns]} capped loading={loading} />
              </Panel>
            )}
            {hasOnlyFuturesAdjustment ? (
              <div className="alert">Only futures adjustment row is present for this filter; no active futures contracts were returned.</div>
            ) : null}
            {activePositionTab === "futures" ? (
              <Panel title="Futures by Commodity">
                <DataTable rows={futuresCommodityRows} columns={["Commodity", "Contracts", "Net_MV"]} loading={loading} />
              </Panel>
            ) : null}
            {activePositionTab === "equity" ? (
              <Panel title="Sector Summary">
                <DataTable
                  rows={filteredSectorSummaryRows}
                  columns={["sector", "Market_Value", "Total_Cost", "PnL", "Alloc_%", "Return_%", "Dividends"]}
                  loading={loading}
                />
              </Panel>
            ) : null}
            {positionBrokerFilter === "COINBASE" && (activePositionTab === "crypto" || activePositionTab === "futures") ? (
              <Panel title="Sector Summary">
                <DataTable
                  rows={filteredSectorSummaryRows}
                  columns={["sector", "Market_Value", "Total_Cost", "PnL", "Alloc_%", "Return_%", "Dividends"]}
                  loading={loading}
                />
              </Panel>
            ) : null}
          </div>
        ) : null}

        {activeTab === "transactions" ? (
          <Panel title={`${transactionFilteredRows.length} Filtered Transactions`}>
            <div className="toolbar">
              <label>Category<select value={txCategory} onChange={(e) => setTxCategory(e.target.value)}><option value="ALL">All</option>{txCategories.map((v) => <option key={v} value={v}>{v}</option>)}</select></label>
              <label>Broker<select value={txBroker} onChange={(e) => setTxBroker(e.target.value)}><option value="ALL">All</option>{txBrokers.map((v) => <option key={v} value={v}>{v}</option>)}</select></label>
              <label>Year<select value={txYear} onChange={(e) => setTxYear(e.target.value)}><option value="ALL">All</option>{txYears.map((v) => <option key={v} value={v}>{v}</option>)}</select></label>
              <label>Search<input value={txSearch} onChange={(e) => setTxSearch(e.target.value)} placeholder="description or symbol" /></label>
              <button type="button" onClick={() => downloadCsv(transactionFilteredRows, "transactions_filtered.csv")}>Export CSV</button>
            </div>
            <DataTable
              rows={transactionFilteredRows}
              columns={["date", "account_id", "broker", "category", "amount", "symbol", "description"]}
              loading={loading}
            />
          </Panel>
        ) : null}

        {activeTab === "performance" ? (
          <div className="stack">
            <Panel title="Portfolio Summary">
              <DataTable rows={performanceSummaryRows} columns={["Account", "Current Value", "1W Ago", "$ Change", "% Change"]} loading={loading} />
            </Panel>
            <Panel title="Portfolio Returns">
              <DataTable rows={performanceReturnRows} columns={["Account", "1-Week", "1-Month", "3-Month", "YTD", "1-Year"]} loading={loading} />
            </Panel>
          </div>
        ) : null}

        {activeTab === "broker" ? (
          <div className="stack">
            <Panel title="MCP Health">
              <DataTable rows={operationHealthRows} columns={["Broker", "Accounts", "Status", "Tools", "Detail"]} loading={loading} />
            </Panel>
            <Panel title="CSV Sync State">
              <DataTable
                rows={csvSyncRows}
                columns={["file_path", "account_id", "file_role", "file_mtime_utc", "last_ingested_at", "rows_written", "status"]}
                loading={loading}
              />
            </Panel>
            <Panel title="Account Sync Status">
              <DataTable
                rows={operationAccountRows}
                columns={["account_id", "broker", "source_signal", "last_synced_ts", "last_snapshot_date", "status_label"]}
                loading={loading}
              />
            </Panel>
          </div>
        ) : null}

        {activeTab === "settings" ? (
          <div className="stack">
            <Panel title="Settings (CLI-managed)">
              <DataTable
                rows={[
                  { item: "Cash Balance (CASH)", value: currency(summary?.data?.live_net_worth ? undefined : undefined), source: "CLI: python -m src.cash / src.cli.main account cash set" },
                  { item: "Fidelity Margin", value: "From Fidelity CSV", source: "CLI/CSV ingest path" },
                ]}
                columns={["item", "value", "source"]}
                loading={loading}
              />
            </Panel>
          </div>
        ) : null}
      </section>
    </main>
  );
}

function KpiRow({ data }: { data?: Record<string, number> }) {
  if (!data) return <div className="empty">No data</div>;
  return (
    <div className="kpiRow">
      {Object.entries(data).map(([label, value]) => (
        <div key={label} className="kpi">
          <span>{label}</span>
          <strong className={Number(value) < 0 ? "neg" : ""}>{currency(value)}</strong>
        </div>
      ))}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="panel">
      <div className="panelHeader">
        <h3>{title}</h3>
      </div>
      {children}
    </section>
  );
}

function capabilityRows(receipt: ApiReceipt<CapabilityPayload> | null, tab: string) {
  const capabilities = receipt?.data?.capabilities ?? [];
  return capabilities
    .filter((capability) => capability.tab === tab)
    .map((capability) => ({
      capability: capability.name,
      source: capability.data_sources.join(", ")
    }));
}

function sumRows(rows: Array<Record<string, unknown>>, column: string) {
  return rows.reduce((total, row) => {
    const value = Number(row[column] ?? 0);
    return total + (Number.isFinite(value) ? value : 0);
  }, 0);
}

function columnsFromRows(rows: Array<Record<string, unknown>>, preferred: string[]) {
  const seen = new Set<string>();
  for (const column of preferred) {
    if (rows.some((row) => Object.prototype.hasOwnProperty.call(row, column))) {
      seen.add(column);
    }
  }
  for (const row of rows) {
    for (const column of Object.keys(row)) {
      seen.add(column);
    }
  }
  return Array.from(seen);
}

function uniqueValues(rows: Array<Record<string, unknown>>, key: string) {
  return Array.from(new Set(rows.map((row) => String(row[key] ?? "")).filter(Boolean))).sort();
}

function groupByAccount(rows: Array<Record<string, unknown>>) {
  const map = new Map<string, Array<Record<string, unknown>>>();
  for (const row of rows) {
    const key = String(row.account_id ?? "UNKNOWN");
    if (!map.has(key)) map.set(key, []);
    map.get(key)?.push(row);
  }
  return Array.from(map.entries());
}

function consolidatePositionsBySymbol(rows: Array<Record<string, unknown>>, assetTab: PositionTabId) {
  const grouped = new Map<string, Record<string, unknown>>();
  for (const row of rows) {
    const key = String(row.symbol ?? "");
    if (!key) continue;
    const existing = grouped.get(key);
    if (!existing) {
      grouped.set(key, { ...row, account_id: "ALL" });
      continue;
    }
    for (const [k, v] of Object.entries(row)) {
      if (["quantity", "cost_basis", "market_value", "unrealized_pnl"].includes(k)) {
        existing[k] = Number(existing[k] ?? 0) + Number(v ?? 0);
      }
    }
    if (assetTab === "equity" || assetTab === "crypto") {
      const qty = Number(existing.quantity ?? 0);
      const mv = Number(existing.market_value ?? 0);
      existing.price = qty !== 0 ? mv / qty : existing.price;
    }
  }
  return Array.from(grouped.values());
}

function downloadCsv(rows: Array<Record<string, unknown>>, filename: string) {
  if (!rows.length) return;
  const columns = Array.from(new Set(rows.flatMap((r) => Object.keys(r))));
  const lines = [columns.join(",")];
  for (const row of rows) {
    const line = columns.map((col) => `"${String(row[col] ?? "").replaceAll("\"", "\"\"")}"`).join(",");
    lines.push(line);
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function SectorPie({ rows }: { rows: Array<Record<string, unknown>> }) {
  const totals = rows.map((row) => ({
    label: String(row.sector ?? "Other"),
    value: Number(row["MARKET VALUE"] ?? 0),
  })).filter((r) => Number.isFinite(r.value) && r.value > 0);
  const sum = totals.reduce((acc, r) => acc + r.value, 0);
  if (!sum) return null;
  let start = 0;
  const colors = ["#126a72", "#2c8b59", "#c2812e", "#b54f3a", "#5b6ca8", "#9a5fa8", "#6d7f40", "#8f4f73"];
  return (
    <div className="pieRow">
      <svg viewBox="0 0 32 32" className="pieChart" aria-label="Sector allocation pie chart">
        {totals.map((slice, idx) => {
          const frac = slice.value / sum;
          const dash = frac * 100;
          const path = <circle key={slice.label} r="16" cx="16" cy="16" fill="transparent" stroke={colors[idx % colors.length]} strokeWidth="32" strokeDasharray={`${dash} ${100 - dash}`} strokeDashoffset={-start} />;
          start += dash;
          return path;
        })}
      </svg>
    </div>
  );
}

function withMarginAssetClass(rows: Array<Record<string, unknown>>, marginValue: unknown) {
  const margin = Number(marginValue ?? 0);
  const nextRows = rows.filter((row) => String(row["Asset Class"] ?? "").toUpperCase() !== "TOTAL");
  if (Number.isFinite(margin) && margin !== 0) {
    nextRows.push({
    "Asset Class": "Margin",
    "Market Value": -Math.abs(margin),
    "Allocation": 0,
    });
  }
  const total = nextRows.reduce((acc, row) => acc + Number(row["Market Value"] ?? 0), 0);
  nextRows.push({
    "Asset Class": "TOTAL",
    "Market Value": total,
    "Allocation": 100,
  });
  return nextRows;
}

function buildSectorSummary(rows: Array<Record<string, unknown>>) {
  const groups = new Map<string, { market: number; cost: number; dividends: number }>();
  for (const row of rows) {
    const sector = String(row.sector ?? "Other");
    const market = Number(row.market_value ?? 0);
    const cost = Number(row.cost_basis ?? 0);
    const dividends = Number(row.dividends ?? 0);
    const current = groups.get(sector) ?? { market: 0, cost: 0, dividends: 0 };
    current.market += Number.isFinite(market) ? market : 0;
    current.cost += Number.isFinite(cost) ? cost : 0;
    current.dividends += Number.isFinite(dividends) ? dividends : 0;
    groups.set(sector, current);
  }
  const totalMarket = Array.from(groups.values()).reduce((acc, g) => acc + g.market, 0);
  return Array.from(groups.entries())
    .map(([sector, value]) => {
      const pnl = value.market - value.cost;
      return {
        sector,
        Market_Value: value.market,
        Total_Cost: value.cost,
        PnL: pnl,
        "Alloc_%": totalMarket !== 0 ? (value.market / totalMarket) * 100 : 0,
        "Return_%": value.cost !== 0 ? (pnl / value.cost) * 100 : 0,
        Dividends: value.dividends,
      };
    })
    .sort((a, b) => Number(b.Market_Value) - Number(a.Market_Value));
}

function normalizeCoinbaseSector(row: Record<string, unknown>) {
  const account = String(row.account_id ?? "");
  if (account !== "COINBASE") return row;
  const symbol = String(row.symbol ?? "").toUpperCase();
  const assetClass = String(row.asset_class ?? "").toLowerCase();
  let sector = "Crypto Derivatives";
  if (symbol === "USD" || symbol === "USDC") {
    sector = "Cash";
  } else if (assetClass === "crypto") {
    sector = "Crypto Derivatives";
  } else if (assetClass === "futures") {
    sector = "Crypto Derivatives";
  }
  return { ...row, sector };
}

function buildFuturesCommodityRows(rows: Array<Record<string, unknown>>) {
  const grouped = new Map<string, { contracts: number; netMv: number }>();
  for (const row of rows) {
    const symbol = String(row.symbol ?? "");
    if (!symbol || symbol === "_FUTURES_ADJ_") continue;
    const commodity = futuresCommodityKey(symbol);
    const current = grouped.get(commodity) ?? { contracts: 0, netMv: 0 };
    current.contracts += 1;
    current.netMv += Number(row.market_value ?? 0);
    grouped.set(commodity, current);
  }
  return Array.from(grouped.entries())
    .map(([Commodity, value]) => ({ Commodity, Contracts: value.contracts, Net_MV: value.netMv }))
    .sort((a, b) => Math.abs(Number(b.Net_MV)) - Math.abs(Number(a.Net_MV)));
}

function futuresCommodityKey(symbol: string) {
  if (symbol.startsWith("/VXM")) return symbol;
  const match = symbol.match(/(\/[A-Z]+)(?=[A-Z]\d{2})/);
  return match ? match[1] : symbol;
}
