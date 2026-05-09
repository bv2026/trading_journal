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

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

const tabs = [
  { id: "portfolio", label: "Portfolio", icon: BriefcaseBusiness },
  { id: "yearly", label: "Yearly Summary", icon: BarChart3 },
  { id: "account", label: "By Account", icon: Landmark },
  { id: "positions", label: "Positions", icon: Table2 },
  { id: "transactions", label: "Transactions", icon: ClipboardList },
  { id: "performance", label: "Performance", icon: LineChart },
  { id: "broker", label: "Broker MCP", icon: DatabaseZap },
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
}: {
  rows: Array<Record<string, unknown>>;
  columns: string[];
  capped?: boolean;
}) {
  if (!rows.length) {
    return <div className="empty">No rows</div>;
  }
  return (
    <div className={`tableWrap${capped ? " capped" : ""}`}>
      <table>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col} className={isNumericColumn(col) ? "num" : ""}>
                {prettyHeader(col)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
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

function formatCell(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "—";
    return Number.isInteger(value) ? value.toLocaleString() : value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
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
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
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
          capabilityData
        ] = await Promise.all([
          getJson<PortfolioSummary>("/portfolio/summary?include_live_net_worth=false"),
          getJson<PositionsPayload>("/portfolio/positions"),
          getJson<DashboardPortfolioPayload>("/dashboard/portfolio"),
          getJson<DashboardPerformancePayload>("/dashboard/performance"),
          getJson<DashboardYearlyPayload>("/dashboard/yearly-summary"),
          getJson<DashboardByAccountPayload>("/dashboard/by-account"),
          getJson<TransactionsPayload>("/transactions?limit=25"),
          getJson<MetricsRow[]>("/portfolio/yearly-summary"),
          getJson<MetricsRow[]>("/portfolio/account-summary"),
          getJson<CapabilityPayload>("/dashboard/capabilities")
        ]);
        if (!cancelled) {
          setSummary(summaryData);
          setPositions(positionsData);
          setDashboardPortfolio(dashboardPortfolioData);
          setDashboardPerformance(dashboardPerformanceData);
          setDashboardYearly(dashboardYearlyData);
          setDashboardByAccount(dashboardByAccountData);
          setTransactions(transactionData);
          setYearlySummary(yearlySummaryData);
          setAccountSummary(accountSummaryData);
          setCapabilities(capabilityData);
          setError(null);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Failed to load API data");
        }
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const positionRows = positions?.data?.canonical_positions ?? [];
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
  const filteredPositionRows = useMemo(
    () => positionRows.filter((row) => String(row.asset_class ?? "").toLowerCase() === activePositionTab),
    [positionRows, activePositionTab]
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
              />
            </Panel>
            <Panel title="Asset Class Breakdown">
              <DataTable
                rows={dashboardPortfolio?.data?.asset_class_breakdown ?? assetRows}
                columns={["Asset Class", "Market Value", "Allocation"]}
              />
            </Panel>
            <Panel title="Futures by Commodity">
              <DataTable rows={dashboardPortfolio?.data?.futures_by_commodity ?? []} columns={["Commodity", "Contracts", "Net_MV"]} />
            </Panel>
            <Panel title="Sector Allocation">
              <DataTable rows={dashboardPortfolio?.data?.sector_allocation ?? []} columns={["sector", "MARKET VALUE"]} />
            </Panel>
            <Panel title={`Positions by Account (${(dashboardPortfolio?.data?.positions_by_account ?? []).length} rows)`}>
              <DataTable
                rows={(dashboardPortfolio?.data?.positions_by_account ?? []).slice(0, 75)}
                columns={["Account", "Ticker", "Name", "TYPE", "sector", "Shares", "PRICE", "COST", "MARKET VALUE", "totalReturn"]}
                capped
              />
            </Panel>
            <Panel title="Sector Summary">
              <DataTable
                rows={dashboardPortfolio?.data?.sector_summary ?? []}
                columns={["sector", "Market_Value", "Total_Cost", "PnL", "Alloc_%", "Return_%", "Dividends"]}
              />
            </Panel>
          </div>
        ) : null}

        {activeTab === "yearly" ? (
          <div className="stack">
            <Panel title="Year-over-Year Summary">
              <DataTable rows={yearlyDashboardRows} columns={columnsFromRows(yearlyDashboardRows, ["Metric", "ALL"])} />
            </Panel>
            <Panel title="Income Breakdown by Type">
              <DataTable rows={incomeBreakdownRows} columns={columnsFromRows(incomeBreakdownRows, ["Type", "ALL"])} />
            </Panel>
            <Panel title={`${yearlyRows.length.toLocaleString()} Canonical Year Rows`}>
              <DataTable
                rows={yearlyRows}
                columns={["label", "net_cash_flow", "dividends", "rewards", "margin_interest", "fees", "net_income"]}
              />
            </Panel>
          </div>
        ) : null}

        {activeTab === "account" ? (
          <div className="stack">
            <Panel title="Net Cash Flow by Account">
              <DataTable rows={byAccountData?.net_cash_flow ?? []} columns={columnsFromRows(byAccountData?.net_cash_flow ?? [], ["Account", "ALL"])} />
            </Panel>
            <Panel title="Div + Rewards by Account">
              <DataTable rows={byAccountData?.div_rewards ?? []} columns={columnsFromRows(byAccountData?.div_rewards ?? [], ["Account", "ALL"])} />
            </Panel>
            <Panel title="Margin + Fees by Account">
              <DataTable rows={byAccountData?.margin_fees ?? []} columns={columnsFromRows(byAccountData?.margin_fees ?? [], ["Account", "ALL"])} />
            </Panel>
            <div className="metricsGrid compact">
              <Metric label="Crypto Total In" value={currency(byAccountData?.crypto_flow.total_in)} />
              <Metric label="Crypto Total Out" value={currency(byAccountData?.crypto_flow.total_out)} />
              <Metric label="Crypto Net Cash" value={currency(byAccountData?.crypto_flow.net)} />
              <Metric label="Accounts" value={accountRows.length.toLocaleString()} />
            </div>
            <Panel title="Crypto Flow Inflows">
              <DataTable rows={byAccountData?.crypto_flow.inflows ?? []} columns={["Type", "Amount", "Txns"]} />
            </Panel>
            <Panel title="Crypto Flow Outflows">
              <DataTable rows={byAccountData?.crypto_flow.outflows ?? []} columns={["Type", "Amount", "Txns"]} />
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
            <div className="metricsGrid compact">
              <Metric label="All Positions" value={positionRows.length.toLocaleString()} />
              <Metric label={`${activePositionConfig.label} Rows`} value={filteredPositionRows.length.toLocaleString()} />
              <Metric label="Market Value" value={currency(sumRows(filteredPositionRows, "market_value"))} />
              <Metric label="Unrealized P/L" value={currency(sumRows(filteredPositionRows, "unrealized_pnl"))} />
            </div>
            <Panel title={`${filteredPositionRows.length.toLocaleString()} ${activePositionConfig.label} Positions`}>
              <DataTable rows={filteredPositionRows} columns={[...activePositionConfig.columns]} capped />
            </Panel>
          </div>
        ) : null}

        {activeTab === "transactions" ? (
          <Panel title={`${transactions?.data?.count ?? 0} Recent Transactions`}>
            <DataTable
              rows={transactionRows}
              columns={["date", "account_id", "broker", "category", "amount", "symbol", "description"]}
            />
          </Panel>
        ) : null}

        {activeTab === "performance" ? (
          <div className="stack">
            <Panel title="Portfolio Summary">
              <DataTable rows={performanceSummaryRows} columns={["Account", "Current Value", "1W Ago", "$ Change", "% Change"]} />
            </Panel>
            <Panel title="Portfolio Returns">
              <DataTable rows={performanceReturnRows} columns={["Account", "1-Week", "1-Month", "3-Month", "YTD", "1-Year"]} />
            </Panel>
          </div>
        ) : null}

        {activeTab === "broker" ? (
          <Panel title="Broker MCP">
            <DataTable rows={capabilityRows(capabilities, "Broker MCP")} columns={["capability", "source"]} />
          </Panel>
        ) : null}

        {activeTab === "settings" ? (
          <Panel title="Settings">
            <DataTable rows={capabilityRows(capabilities, "Settings")} columns={["capability", "source"]} />
          </Panel>
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
