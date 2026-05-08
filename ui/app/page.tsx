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

function currency(value: unknown) {
  const number = Number(value ?? 0);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0
  }).format(Number.isFinite(number) ? number : 0);
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
  columns
}: {
  rows: Array<Record<string, unknown>>;
  columns: string[];
}) {
  if (!rows.length) {
    return <div className="empty">No rows</div>;
  }
  return (
    <div className="tableWrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {columns.map((column) => (
                <td key={column}>{formatCell(row[column])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(value: unknown) {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(2);
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
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
  const [summary, setSummary] = useState<ApiReceipt<PortfolioSummary> | null>(null);
  const [positions, setPositions] = useState<ApiReceipt<PositionsPayload> | null>(null);
  const [transactions, setTransactions] = useState<ApiReceipt<TransactionsPayload> | null>(null);
  const [performance, setPerformance] = useState<ApiReceipt<Array<Record<string, unknown>>> | null>(null);
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
          transactionData,
          performanceData,
          yearlySummaryData,
          accountSummaryData,
          capabilityData
        ] = await Promise.all([
          getJson<PortfolioSummary>("/portfolio/summary?include_live_net_worth=false"),
          getJson<PositionsPayload>("/portfolio/positions"),
          getJson<TransactionsPayload>("/transactions?limit=25"),
          getJson<Array<Record<string, unknown>>>("/portfolio/performance"),
          getJson<MetricsRow[]>("/portfolio/yearly-summary"),
          getJson<MetricsRow[]>("/portfolio/account-summary"),
          getJson<CapabilityPayload>("/dashboard/capabilities")
        ]);
        if (!cancelled) {
          setSummary(summaryData);
          setPositions(positionsData);
          setTransactions(transactionData);
          setPerformance(performanceData);
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
  const performanceRows = performance?.data ?? [];
  const yearlyRows = yearlySummary?.data ?? [];
  const accountRows = accountSummary?.data ?? [];
  const assetRows = useMemo(() => {
    const rows = positions?.data?.summary?.by_asset_class;
    return Array.isArray(rows) ? (rows as Array<Record<string, unknown>>) : [];
  }, [positions]);

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
              <Metric label="Net Cash Flow" value={currency(summary?.data?.net_cash_flow)} />
              <Metric label="Dividends" value={currency(summary?.data?.dividends)} />
              <Metric label="Rewards" value={currency(summary?.data?.rewards)} />
              <Metric label="Net Income" value={currency(summary?.data?.net_income)} />
            </div>
            <Panel title="Asset Class Breakdown">
              <DataTable rows={assetRows} columns={["asset_class", "count", "market_value"]} />
            </Panel>
          </div>
        ) : null}

        {activeTab === "yearly" ? (
          <Panel title={`${yearlyRows.length.toLocaleString()} Yearly Summary Rows`}>
            <DataTable
              rows={yearlyRows}
              columns={["label", "net_cash_flow", "dividends", "rewards", "margin_interest", "fees", "net_income"]}
            />
          </Panel>
        ) : null}

        {activeTab === "account" ? (
          <Panel title={`${accountRows.length.toLocaleString()} Account Summary Rows`}>
            <DataTable
              rows={accountRows}
              columns={["label", "broker", "net_cash_flow", "dividends", "rewards", "margin_interest", "fees", "net_income"]}
            />
          </Panel>
        ) : null}

        {activeTab === "positions" ? (
          <Panel title={`${positionRows.length.toLocaleString()} Positions`}>
            <DataTable
              rows={positionRows.slice(0, 50)}
              columns={["account_id", "symbol", "asset_class", "quantity", "price", "market_value", "unrealized_pnl", "sector"]}
            />
          </Panel>
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
          <Panel title="Portfolio Performance">
            <DataTable rows={performanceRows} columns={["account_id", "current_value", "returns"]} />
          </Panel>
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
