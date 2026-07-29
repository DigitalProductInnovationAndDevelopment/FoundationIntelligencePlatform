import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

type CurrencyFormatter = (value: number | null | undefined, currency?: string) => string;

interface GrantTrendPoint {
  month: string;
  grant_count: number | null;
  total_amount: number | null;
  coverage_status: "observed" | "partial" | "unknown";
}

interface ProgrammeAllocationPoint {
  programme_area: string;
  allocated_amount: number;
}

interface FinancialHistoryPoint {
  year: string | number;
  Income: number | null;
  Expenditure: number | null;
}

export function GrantAwardsChart({
  items,
  currency,
  formatCurrency,
}: {
  items: GrantTrendPoint[];
  currency: string;
  formatCurrency: CurrencyFormatter;
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={items}>
        <defs>
          <linearGradient id="colorGrantAwards" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--nl-unicorn)" stopOpacity={0.3} />
            <stop offset="95%" stopColor="var(--nl-unicorn)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" />
        <XAxis dataKey="month" stroke="var(--text-muted)" fontSize={10} tickFormatter={month => String(month).slice(2)} />
        <YAxis stroke="var(--text-muted)" fontSize={11} tickFormatter={value => formatCurrency(Number(value), currency)} />
        <Tooltip
          filterNull={false}
          content={({ active, payload, label }) => {
            if (!active || !payload?.length) return null;
            const item = payload[0]?.payload as GrantTrendPoint;
            return (
              <div className="chart-tooltip">
                <strong>{label}</strong>
                {item.coverage_status === "unknown" ? (
                  <span>No source coverage established; not a confirmed zero.</span>
                ) : item.coverage_status === "partial" ? (
                  <span>Source records exist, but no valid amount can be aggregated.</span>
                ) : (
                  <><span>{formatCurrency(item.total_amount, currency)}</span><span>{item.grant_count} recorded grants</span></>
                )}
              </div>
            );
          }}
        />
        <Area
          type="monotone"
          dataKey="total_amount"
          name="Recorded grant awards"
          connectNulls={false}
          stroke="var(--nl-unicorn)"
          fillOpacity={1}
          fill="url(#colorGrantAwards)"
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function ProgrammeAllocationChart({
  items,
  currency,
  formatCurrency,
}: {
  items: ProgrammeAllocationPoint[];
  currency: string;
  formatCurrency: CurrencyFormatter;
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={items} layout="vertical" margin={{ left: 25, right: 30 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" />
        <XAxis type="number" stroke="var(--text-muted)" fontSize={11} tickFormatter={value => formatCurrency(Number(value), currency)} />
        <YAxis type="category" width={210} dataKey="programme_area" stroke="var(--text-muted)" fontSize={11} />
        <Tooltip
          formatter={value => formatCurrency(Number(value), currency)}
          labelFormatter={label => String(label)}
          contentStyle={{ backgroundColor: "var(--bg-surface-opaque)", borderColor: "var(--border-glass)" }}
        />
        <Bar dataKey="allocated_amount" name="Allocated source amount" fill="var(--nl-unicorn)" radius={[0, 4, 4, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function FinancialHistoryChart({
  data,
  formatCurrency,
}: {
  data: FinancialHistoryPoint[];
  formatCurrency: CurrencyFormatter;
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
        <defs>
          <linearGradient id="colorInc" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--nl-unicorn)" stopOpacity={0.2} />
            <stop offset="95%" stopColor="var(--nl-unicorn)" stopOpacity={0} />
          </linearGradient>
          <linearGradient id="colorExp" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--nl-sunny)" stopOpacity={0.2} />
            <stop offset="95%" stopColor="var(--nl-sunny)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="year" fontSize={11} tickLine={false} />
        <YAxis fontSize={11} tickLine={false} tickFormatter={value => formatCurrency(Number(value)).replace("€", "")} />
        <Tooltip formatter={value => formatCurrency(Number(value))} contentStyle={{ backgroundColor: "var(--bg-surface-opaque)", borderColor: "var(--border-glass)" }} />
        <Area type="monotone" dataKey="Income" stroke="var(--nl-unicorn)" fillOpacity={1} fill="url(#colorInc)" strokeWidth={2} isAnimationActive={false} />
        <Area type="monotone" dataKey="Expenditure" stroke="var(--nl-sunny)" fillOpacity={1} fill="url(#colorExp)" strokeWidth={2} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
