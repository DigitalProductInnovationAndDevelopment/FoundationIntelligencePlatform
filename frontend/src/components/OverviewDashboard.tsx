import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, LoaderCircle, SlidersHorizontal, Star, X } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import GrantWorldMap, {
  type GrantMapFilters,
  type GrantMapResponse,
  type MapCountrySelection,
  type SourceFunderCountrySelection,
} from "./GrantWorldMap";
import {
  applyGrantScopeToParams,
  grantScopeChips,
  grantScopeFromUrl,
  grantScopeToApiParams,
  type GrantScope,
} from "../lib/grantScope";

type PeriodPreset = "all" | "last12" | "last24" | "currentYear" | "custom";
type Granularity = "auto" | "monthly" | "yearly";

export interface OverviewFilters {
  currency: string;
  dateFrom: string;
  dateTo: string;
  beneficiaryGeographies: string[];
  programmeAreas: string[];
  donor: string;
  recipient: string;
  granularity: Granularity;
  periodPreset: PeriodPreset;
}

interface TrendItem {
  month: string;
  grant_count: number | null;
  source_record_count: number;
  total_amount: number | null;
  coverage_status: "observed" | "partial" | "unknown";
  mapped_grant_count?: number;
  unmapped_grant_count?: number;
}

interface ThemeItem {
  programme_area: string;
  allocated_amount: number;
  distinct_grant_count: number;
  unclassified_grant_count: number;
}

interface OverviewPayload {
  status: string;
  kpis: {
    awarded_funding: number | null;
    currency: string | null;
    grants_monitored: number;
    country_coverage_percentage: number;
    mapped_grant_count: number;
    unmapped_grant_count: number;
    programme_coverage_percentage: number;
    classified_grant_count: number;
    qualifying_programme_grant_count: number;
  };
  map: GrantMapResponse;
  trends: {
    status: string;
    currency: string | null;
    available_currencies: string[];
    granularity: "monthly" | "yearly";
    period: { from: string; to: string; months: number } | null;
    items: TrendItem[];
    excluded: Record<string, number>;
    last_refreshed_at: string | null;
  };
  themes: {
    status: string;
    currency: string | null;
    items: ThemeItem[];
    classification_coverage: {
      qualifying_grant_count: number;
      classified_grant_count: number;
      unclassified_grant_count: number;
      classified_percentage: number;
    };
    qualifying_amount: number;
    allocated_amount: number;
  };
  available_date_range: { from: string | null; to: string | null };
}

interface Props {
  apiBase: string;
  online: boolean;
  selectedSources: string[];
  onOpenOrganizationDirectory: (filters: GrantMapFilters) => void;
  onExploreSourceFunders: (selection: SourceFunderCountrySelection, filters: OverviewFilters) => void;
  onToggleFavoriteLandscape: (filters: OverviewFilters) => void;
  isFavoriteLandscape: (filters: OverviewFilters) => boolean;
}

const PROGRAMMES = [
  "Socio-economic Development, Poverty", "Environment/Climate", "Youth/Children Development",
  "Food, Agriculture & Nutrition", "tech-enablement", "Sciences & Research", "Health",
  "Arts & Culture", "Humanitarian & Disaster Relief", "Human/Civil Rights", "Diversity & Inclusion",
  "Civil society, Voluntarism & Non-Profit Sector", "Citizenship, Social Justice & Public Affairs",
  "Peace & Conflict Resolution",
];
const BENEFICIARY_GEOGRAPHIES = ["United Kingdom", "Ghana", "Kenya", "Tanzania", "Uganda", "South Africa", "India", "Worldwide", "Europe (DACH)"];
const EMPTY_FILTERS: OverviewFilters = {
  currency: "", dateFrom: "", dateTo: "", beneficiaryGeographies: [], programmeAreas: [], donor: "", recipient: "", granularity: "auto", periodPreset: "all",
};
const EMPTY_MAP: GrantMapResponse = {
  status: "no_data", geographic_dimension: "beneficiary_location", items: [], known_geography_count: 0,
  unknown_geography_count: 0, coverage_percentage: 0, currencies: [], selected_currency: null,
  funding_status: "unavailable", funding_mode_available: false, grant_country_association_count: 0,
  multi_country_grant_count: 0, funding_excluded_multi_country_count: 0, funding_excluded_multi_country_amount: 0,
  funding_excluded_currency_count: 0, funding_excluded_invalid_amount_count: 0, connections: [],
  connection_grant_count: 0, connection_excluded_no_headquarters_count: 0, connection_same_country_count: 0,
  minimum_coverage_threshold: 0,
  metadata: { data_mode: "unavailable", source: [], record_count: 0, limitations: [] },
};

function formatCurrency(value: number | null, currency: string | null) {
  if (value === null || value === undefined || !currency) return "Unavailable";
  return new Intl.NumberFormat("en-GB", { style: "currency", currency, notation: value >= 1_000_000 ? "compact" : "standard", maximumFractionDigits: value >= 1_000_000 ? 1 : 0 }).format(value);
}

function TrendLoadingState({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`trend-loading-state${compact ? " compact" : ""}`} role="status" aria-live="polite">
      <LoaderCircle size={compact ? 18 : 26} aria-hidden="true" />
      <div>
        <strong>{compact ? "Updating grant awards" : "Preparing grant awards over time"}</strong>
        <span>{compact ? "Applying the selected period and granularity" : "Matching the selected grants and calculating award periods"}</span>
      </div>
    </div>
  );
}

function shiftMonths(endDate: string, months: number) {
  const date = new Date(`${endDate}T00:00:00`);
  date.setMonth(date.getMonth() - months);
  return date.toISOString().slice(0, 10);
}

function filtersFromUrl(): OverviewFilters {
  const params = new URLSearchParams(window.location.search);
  const scope = grantScopeFromUrl(params);
  const dateFrom = scope.dateFrom || "";
  const dateTo = scope.dateTo || "";
  return {
    currency: scope.currency || "",
    dateFrom,
    dateTo,
    beneficiaryGeographies: scope.beneficiaryGeographies,
    programmeAreas: scope.programmeAreas,
    donor: scope.donor || "",
    recipient: scope.recipient || "",
    granularity: (["auto", "monthly", "yearly"].includes(params.get("grant_granularity") || "") ? params.get("grant_granularity") : "auto") as Granularity,
    periodPreset: dateFrom || dateTo ? "custom" : "all",
  };
}

export default function OverviewDashboard({ apiBase, online, selectedSources, onOpenOrganizationDirectory, onExploreSourceFunders, onToggleFavoriteLandscape, isFavoriteLandscape }: Props) {
  const [filters, setFilters] = useState<OverviewFilters>(filtersFromUrl);
  const [draft, setDraft] = useState<OverviewFilters>(filters);
  const [payload, setPayload] = useState<OverviewPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [showAllProgrammes, setShowAllProgrammes] = useState(false);
  const [includeUnclassified, setIncludeUnclassified] = useState(true);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [selectedMapCountryCode, setSelectedMapCountryCode] = useState<string | null>(null);
  const [includeConnections, setIncludeConnections] = useState(false);
  const [trendPeriodOpen, setTrendPeriodOpen] = useState(false);
  const [trendDateFrom, setTrendDateFrom] = useState(filters.dateFrom);
  const [trendDateTo, setTrendDateTo] = useState(filters.dateTo);
  const [trendOverride, setTrendOverride] = useState<OverviewPayload["trends"] | null>(null);
  const [trendLoading, setTrendLoading] = useState(false);
  const requestVersion = useRef(0);
  const trendRequestVersion = useRef(0);
  const drawerRef = useRef<HTMLElement>(null);
  const activeFilterCount = Number(Boolean(filters.currency)) + Number(Boolean(filters.dateFrom || filters.dateTo))
    + filters.beneficiaryGeographies.length + filters.programmeAreas.length
    + Number(Boolean(filters.donor.trim())) + Number(Boolean(filters.recipient.trim()))
    + Number(filters.granularity !== "auto");

  const updateUrl = useCallback((next: OverviewFilters) => {
    const query = applyGrantScopeToParams(
      new URLSearchParams(window.location.search),
      {
        currency: next.currency || undefined,
        dateFrom: next.dateFrom || undefined,
        dateTo: next.dateTo || undefined,
        beneficiaryGeographies: next.beneficiaryGeographies,
        programmeAreas: next.programmeAreas,
        donor: next.donor,
        recipient: next.recipient,
        sources: selectedSources,
      },
      { includeCountry: false, persistEmptySources: true },
    );
    const assign = (key: string, value: string) => value ? query.set(key, value) : query.delete(key);
    assign("grant_granularity", next.granularity === "auto" ? "" : next.granularity);
    const suffix = query.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${suffix ? `?${suffix}` : ""}`);
  }, [selectedSources]);

  // Granularity affects only the time-series presentation. The map, KPIs and
  // programme allocation can keep their cached Auto result while the chart
  // asks for its compact trend-only response.
  const overviewRequestFilters = useMemo(() => ({
    currency: filters.currency,
    dateFrom: filters.dateFrom,
    dateTo: filters.dateTo,
    beneficiaryGeographies: filters.beneficiaryGeographies,
    programmeAreas: filters.programmeAreas,
    donor: filters.donor,
    recipient: filters.recipient,
  }), [
    filters.currency,
    filters.dateFrom,
    filters.dateTo,
    filters.beneficiaryGeographies,
    filters.programmeAreas,
    filters.donor,
    filters.recipient,
  ]);

  useEffect(() => {
    if (!online) return;
    const controller = new AbortController();
    const currentVersion = ++requestVersion.current;
    const requestScope: GrantScope = {
      currency: overviewRequestFilters.currency || undefined,
      dateFrom: overviewRequestFilters.dateFrom || undefined,
      dateTo: overviewRequestFilters.dateTo || undefined,
      beneficiaryGeographies: overviewRequestFilters.beneficiaryGeographies,
      programmeAreas: overviewRequestFilters.programmeAreas,
      donor: overviewRequestFilters.donor,
      recipient: overviewRequestFilters.recipient,
      sources: selectedSources,
    };
    const params = grantScopeToApiParams(requestScope);
    params.set("granularity", "auto");
    if (includeConnections) params.set("include_connections", "true");
    setLoading(true);
    fetch(`${apiBase}/api/charities/grants/overview?${params.toString()}`, { credentials: "include", signal: controller.signal })
      .then(async response => {
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || `Overview request failed (${response.status}).`);
        return result as OverviewPayload;
      })
      .then(result => {
        if (currentVersion !== requestVersion.current) return;
        setPayload(result);
        setError(null);
      })
      .catch(requestError => {
        if ((requestError as Error).name !== "AbortError" && currentVersion === requestVersion.current) setError((requestError as Error).message);
      })
      .finally(() => {
        if (currentVersion === requestVersion.current) setLoading(false);
      });
    return () => controller.abort();
  }, [apiBase, includeConnections, online, overviewRequestFilters, refreshNonce, selectedSources]);

  useEffect(() => {
    if (!online || filters.granularity === "auto") {
      setTrendOverride(null);
      setTrendLoading(false);
      return;
    }
    const controller = new AbortController();
    const currentVersion = ++trendRequestVersion.current;
    const params = grantScopeToApiParams({
      currency: filters.currency || undefined,
      dateFrom: filters.dateFrom || undefined,
      dateTo: filters.dateTo || undefined,
      beneficiaryGeographies: filters.beneficiaryGeographies,
      programmeAreas: filters.programmeAreas,
      donor: filters.donor,
      recipient: filters.recipient,
      sources: selectedSources,
    });
    params.set("granularity", filters.granularity);
    setTrendLoading(true);
    fetch(`${apiBase}/api/charities/grants/overview/trends?${params.toString()}`, { credentials: "include", signal: controller.signal })
      .then(async response => {
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || `Trend request failed (${response.status}).`);
        return result as OverviewPayload["trends"];
      })
      .then(result => {
        if (currentVersion !== trendRequestVersion.current) return;
        setTrendOverride(result);
        setError(null);
      })
      .catch(requestError => {
        if ((requestError as Error).name !== "AbortError" && currentVersion === trendRequestVersion.current) setError((requestError as Error).message);
      })
      .finally(() => {
        if (currentVersion === trendRequestVersion.current) setTrendLoading(false);
      });
    return () => controller.abort();
  }, [apiBase, filters, online, selectedSources]);

  useEffect(() => {
    updateUrl(filters);
    window.dispatchEvent(new CustomEvent("overview-filter-count", { detail: activeFilterCount }));
  }, [activeFilterCount, filters, updateUrl]);

  useEffect(() => {
    const restoreFromHistory = () => {
      const next = filtersFromUrl();
      setFilters(next);
      setDraft(next);
      setSelectedMapCountryCode(null);
    };
    window.addEventListener("popstate", restoreFromHistory);
    return () => window.removeEventListener("popstate", restoreFromHistory);
  }, []);

  useEffect(() => {
    window.dispatchEvent(new CustomEvent("overview-filter-drawer-state", { detail: drawerOpen }));
    return () => {
      window.dispatchEvent(new CustomEvent("overview-filter-drawer-state", { detail: false }));
    };
  }, [drawerOpen]);

  const closeDrawer = () => {
    setDrawerOpen(false);
    window.setTimeout(() => document.querySelector<HTMLButtonElement>(".header-overview-filter")?.focus(), 0);
  };

  useEffect(() => {
    const openFilters = () => {
      setDraft(filters);
      setDrawerOpen(true);
    };
    window.addEventListener("overview-open-filters", openFilters);
    return () => window.removeEventListener("overview-open-filters", openFilters);
  }, [filters]);

  useEffect(() => {
    const resetOverviewFilters = () => {
      const next = { ...EMPTY_FILTERS };
      setFilters(next);
      setDraft(next);
      setError(null);
      setDrawerOpen(false);
      setSelectedMapCountryCode(null);
    };
    window.addEventListener("overview-reset-filters", resetOverviewFilters);
    return () => window.removeEventListener("overview-reset-filters", resetOverviewFilters);
  }, []);

  useEffect(() => {
    const refreshOverview = () => setRefreshNonce(current => current + 1);
    window.addEventListener("overview-refresh", refreshOverview);
    return () => window.removeEventListener("overview-refresh", refreshOverview);
  }, []);

  useEffect(() => {
    if (!drawerOpen) return;
    drawerRef.current?.focus();
    const keepFocusInDrawer = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeDrawer();
        return;
      }
      if (event.key !== "Tab" || !drawerRef.current) return;
      const focusable = Array.from(drawerRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      ));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", keepFocusInDrawer);
    return () => window.removeEventListener("keydown", keepFocusInDrawer);
  }, [drawerOpen]);

  const applyPreset = (preset: PeriodPreset, current = draft): OverviewFilters => {
    const available = payload?.available_date_range;
    if (preset === "all" || !available?.from || !available.to) return { ...current, periodPreset: preset, dateFrom: "", dateTo: "" };
    if (preset === "last12") return { ...current, periodPreset: preset, dateFrom: shiftMonths(available.to, 11), dateTo: available.to };
    if (preset === "last24") return { ...current, periodPreset: preset, dateFrom: shiftMonths(available.to, 23), dateTo: available.to };
    if (preset === "currentYear") {
      const proposedStart = `${new Date().getFullYear()}-01-01`;
      const boundedStart = proposedStart > available.to
        ? available.from
        : proposedStart < available.from ? available.from : proposedStart;
      return { ...current, periodPreset: preset, dateFrom: boundedStart, dateTo: available.to };
    }
    return { ...current, periodPreset: "custom" };
  };

  const applyFilters = () => {
    if (draft.dateFrom && draft.dateTo && draft.dateFrom > draft.dateTo) {
      setError("The period start cannot be after the period end.");
      return;
    }
    setFilters(draft);
    if (draft.beneficiaryGeographies.join("|") !== filters.beneficiaryGeographies.join("|")) {
      setSelectedMapCountryCode(null);
    }
    closeDrawer();
  };

  const openTrendCustomPeriod = () => {
    setTrendDateFrom(filters.dateFrom || payload?.available_date_range.from || "");
    setTrendDateTo(filters.dateTo || payload?.available_date_range.to || "");
    setTrendPeriodOpen(true);
  };

  const applyTrendCustomPeriod = () => {
    if (trendDateFrom && trendDateTo && trendDateFrom > trendDateTo) {
      setError("The period start cannot be after the period end.");
      return;
    }
    const next = {
      ...filters,
      dateFrom: trendDateFrom,
      dateTo: trendDateTo,
      periodPreset: "custom" as const,
    };
    setFilters(next);
    setDraft(next);
    setError(null);
    setTrendPeriodOpen(false);
  };

  const visibleThemeItems = useMemo(() => {
    const items = (payload?.themes.items || []).filter(item => includeUnclassified || item.programme_area !== "Unclassified");
    if (showAllProgrammes || items.length <= 9) return items;
    const top = items.filter(item => item.programme_area !== "Unclassified").slice(0, 8);
    const remainder = items.filter(item => !top.includes(item));
    const otherAmount = remainder.reduce((sum, item) => sum + item.allocated_amount, 0);
    return otherAmount ? [...top, { programme_area: "Other", allocated_amount: otherAmount, distinct_grant_count: remainder.reduce((sum, item) => sum + item.distinct_grant_count, 0), unclassified_grant_count: 0 }] : top;
  }, [includeUnclassified, payload?.themes.items, showAllProgrammes]);

  const beneficiaryOptions = useMemo(
    () => Array.from(new Set([
      ...BENEFICIARY_GEOGRAPHIES,
      ...(payload?.map.items.map(item => item.region_or_country_name) || []),
      ...filters.beneficiaryGeographies,
      ...draft.beneficiaryGeographies,
    ])).sort((left, right) => left.localeCompare(right)),
    [draft.beneficiaryGeographies, filters.beneficiaryGeographies, payload?.map.items],
  );

  const selectMapCountry = (selection: MapCountrySelection | null) => {
    setSelectedMapCountryCode(selection?.countryCode || null);
    setFilters(current => {
      const next = { ...current, beneficiaryGeographies: selection ? [selection.countryName] : [] };
      setDraft(next);
      return next;
    });
  };

  // The Organization Directory has independent organization-level semantics.
  // Only beneficiary geography is safely carried from a country selection.
  const legacyMapFilters: GrantMapFilters = { search: "", tags: [], foundationRegions: [], fundingRegions: filters.beneficiaryGeographies, minAnnualGiving: 0, minAvgGrantSize: 0 };
  const kpis = payload?.kpis;
  const trends = trendOverride || payload?.trends;
  const trendIsLoading = trendLoading || (loading && filters.granularity === "auto");
  const themes = payload?.themes;
  const chartPeriod = trends?.period ? `${trends.period.from}–${trends.period.to}` : "Selected period";
  const overviewScopeChips = grantScopeChips({
    currency: filters.currency || undefined,
    dateFrom: filters.dateFrom || undefined,
    dateTo: filters.dateTo || undefined,
    beneficiaryGeographies: filters.beneficiaryGeographies,
    programmeAreas: filters.programmeAreas,
    donor: filters.donor,
    recipient: filters.recipient,
    sources: selectedSources,
  }).filter(chip => chip.key !== "beneficiaryGeographies");
  const landscapeIsFavorite = isFavoriteLandscape(filters);

  return (
    <div className="overview-dashboard">
      <div className="overview-favorite-toolbar">
        <span>Funding Landscape</span>
        <button
          type="button"
          className={`favorite-toggle${landscapeIsFavorite ? " is-favorite" : ""}`}
          aria-label={`${landscapeIsFavorite ? "Remove current funding landscape from" : "Add current funding landscape to"} favorites`}
          aria-pressed={landscapeIsFavorite}
          onClick={() => onToggleFavoriteLandscape(filters)}
        ><Star size={16} fill={landscapeIsFavorite ? "currentColor" : "none"} /></button>
      </div>
      {overviewScopeChips.length > 0 && <div className="active-filter-chips" aria-label="Active Funding Landscape filters">
        {overviewScopeChips.slice(0, 3).map(chip => <span key={`${chip.key}-${chip.label}`}>{chip.label}</span>)}
        {overviewScopeChips.length > 3 && <button type="button" onClick={() => window.dispatchEvent(new Event("overview-open-filters"))}>+{overviewScopeChips.length - 3} more</button>}
      </div>}
      {error && <div className="data-notice data-notice-warning">{error}</div>}

      <div className="overview-kpi-grid compact landscape-metrics">
        <div className="glass-card overview-kpi"><span>Awarded funding</span><strong>{formatCurrency(kpis?.awarded_funding ?? null, kpis?.currency ?? null)}</strong><small>{filters.currency ? `${kpis?.currency || filters.currency} original source amounts` : "Auto · ECB-converted EUR"}</small></div>
        <div className="glass-card overview-kpi"><span>Grants monitored</span><strong>{kpis?.grants_monitored?.toLocaleString("en-GB") ?? "—"}</strong><small>Active grant scope</small></div>
        <div className="glass-card overview-kpi"><span>Observed funder scope</span><strong>{payload?.map.items.reduce((sum, item) => sum + item.distinct_funders, 0).toLocaleString("en-GB") ?? "—"}</strong><small>Country-level source funder associations</small></div>
        <div className="glass-card overview-kpi"><span>Project coverage</span><strong>{kpis?.programme_coverage_percentage != null ? `${kpis.programme_coverage_percentage}%` : "—"}</strong><small>{kpis?.classified_grant_count?.toLocaleString("en-GB") ?? "—"} classified grants</small></div>
      </div>

      <GrantWorldMap
        data={payload?.map || EMPTY_MAP}
        loading={loading && !payload}
        error={null}
        filters={legacyMapFilters}
        onOpenOrganizationDirectory={onOpenOrganizationDirectory}
        onExploreSourceFunders={(selection) => onExploreSourceFunders(selection, filters)}
        selectedCountryCode={selectedMapCountryCode}
        onCountrySelectionChange={selectMapCountry}
        refreshing={loading && Boolean(payload)}
        onConnectionsVisibilityChange={setIncludeConnections}
      />

      <div className="analytics-charts-grid overview-analytics-grid">
        <section className="glass-card analytics-chart-card compact-chart" aria-labelledby="grant-trend-title">
          <div className="chart-card-header">
            <div><h3 id="grant-trend-title">Grant Awards Over Time</h3><span>{trends?.granularity === "yearly" ? "Annual" : "Monthly"} · {chartPeriod} · {filters.currency ? `${trends?.currency || filters.currency} original` : "EUR · Auto (ECB converted)"}</span></div>
            <div className="chart-segmented" role="group" aria-label="Grant trend granularity">
              {(["auto", "monthly", "yearly"] as Granularity[]).map(option => <button type="button" className={filters.granularity === option ? "active" : ""} key={option} onClick={() => setFilters(current => ({ ...current, granularity: option }))}>{option === "auto" ? "Auto" : option === "monthly" ? "Monthly" : "Yearly"}</button>)}
              <button type="button" className={filters.periodPreset === "custom" ? "active" : ""} onClick={openTrendCustomPeriod}>Custom period</button>
            </div>
          </div>
          {trendPeriodOpen && <div className="trend-period-picker" role="group" aria-label="Custom grant award period">
            <label><span>From</span><input type="date" min={payload?.available_date_range.from || undefined} max={payload?.available_date_range.to || undefined} value={trendDateFrom} onChange={event => setTrendDateFrom(event.target.value)} /></label>
            <label><span>To</span><input type="date" min={trendDateFrom || payload?.available_date_range.from || undefined} max={payload?.available_date_range.to || undefined} value={trendDateTo} onChange={event => setTrendDateTo(event.target.value)} /></label>
            <p>This range also updates the map and programme allocation.</p>
            <div><button type="button" onClick={() => setTrendPeriodOpen(false)}>Cancel</button><button type="button" className="btn btn-primary" onClick={applyTrendCustomPeriod}>Apply period</button></div>
          </div>}
          {trendIsLoading && !trends ? <TrendLoadingState /> : trends?.status === "available" && trends.items.length ? <>
            <p className="visually-hidden">Grant awards are shown for {trends.items.length} {trends.granularity} periods in {trends.currency}. Use the chart tooltip to inspect total awarded funding, grant count, and mapped versus unmapped grants for each period.</p>
            <div className={`analytics-chart-plot compact trend-chart-plot${trendIsLoading ? " is-refreshing" : ""}`}><ResponsiveContainer width="100%" height="100%"><BarChart data={trends.items} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}><CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,.09)" /><XAxis dataKey="month" minTickGap={28} tick={{ fill: "#707070", fontSize: 10 }} tickFormatter={value => String(value).slice(2)} /><YAxis width={58} tick={{ fill: "#707070", fontSize: 10 }} tickFormatter={value => formatCurrency(Number(value), trends.currency).replace("£", "£")} /><Tooltip content={({ active, payload: rows, label }) => { const item = rows?.[0]?.payload as TrendItem | undefined; if (!active || !item) return null; return <div className="chart-tooltip"><strong>{label}</strong>{item.coverage_status === "observed" ? <><span>{formatCurrency(item.total_amount, trends.currency)}</span><span>{item.grant_count} grants · {item.mapped_grant_count} mapped · {item.unmapped_grant_count} unmapped</span></> : <span>{item.coverage_status === "partial" ? "Source records without a valid aggregate." : "No source coverage established; not a confirmed zero."}</span>}</div>; }} /><Bar dataKey="total_amount" name="Awarded funding" fill="var(--nl-chart-primary)" radius={[4, 4, 0, 0]} isAnimationActive={false} /></BarChart></ResponsiveContainer>{trendIsLoading && <div className="trend-chart-refresh"><TrendLoadingState compact /></div>}</div>
            <details className="chart-methodology"><summary>Methodology and data coverage</summary><p>Award-date aggregation from the filtered 360Giving grant population. Auto uses the stored ECB reference rate for the award date, or the preceding ECB business day; empty periods are never shown as zero funding.</p></details>
          </> : <div className="data-notice data-notice-warning">No qualifying grant awards are available for the selected filters.</div>}
        </section>

        <section className="glass-card analytics-chart-card compact-chart" aria-labelledby="programme-chart-title">
          <div className="chart-card-header"><div><h3 id="programme-chart-title">Grant Allocation by Programme Area</h3><span>Programme coverage: {themes?.classification_coverage.classified_percentage ?? "—"}% · {themes?.classification_coverage.classified_grant_count ?? 0} classified</span></div><div className="chart-actions"><button type="button" aria-pressed={includeUnclassified} onClick={() => setIncludeUnclassified(current => !current)}>{includeUnclassified ? "Classified only" : "Include unclassified"}</button><button type="button" onClick={() => setShowAllProgrammes(current => !current)}>{showAllProgrammes ? "Top categories" : "Show all"}</button></div></div>
          {loading && !themes ? <div className="chart-loading"><LoaderCircle size={20} /> Loading programme allocation…</div> : themes?.status === "available" && visibleThemeItems.length ? <>
            <p className="visually-hidden">Programme allocation is shown across {visibleThemeItems.length} categories in {themes.currency}. Programme classification coverage is {themes.classification_coverage.classified_percentage} percent.</p>
            <div className="analytics-chart-plot compact"><ResponsiveContainer width="100%" height="100%"><BarChart data={visibleThemeItems} layout="vertical" margin={{ top: 0, right: 10, bottom: 0, left: 12 }}><CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,.09)" /><XAxis type="number" tick={{ fill: "#707070", fontSize: 10 }} tickFormatter={value => formatCurrency(Number(value), themes.currency).replace("£", "£")} /><YAxis type="category" width={125} dataKey="programme_area" tick={{ fill: "#3f3f3f", fontSize: 10 }} /><Tooltip formatter={(value) => formatCurrency(Number(value), themes.currency)} /><Bar dataKey="allocated_amount" radius={[0, 4, 4, 0]} isAnimationActive={false}>{visibleThemeItems.map(item => <Cell key={item.programme_area} fill={item.programme_area === "Unclassified" ? "#a6a6a6" : item.programme_area === "Other" ? "#c6c6ff" : "#a29aff"} />)}</Bar></BarChart></ResponsiveContainer></div>
            {!includeUnclassified && themes.classification_coverage.unclassified_grant_count > 0 && <p className="chart-coverage-note">Excluded from ranking: {themes.classification_coverage.unclassified_grant_count} unclassified grants. Programme coverage remains {themes.classification_coverage.classified_percentage}%.</p>}
            <details className="chart-methodology"><summary>Methodology and data coverage</summary><p>Source categories take precedence over accepted inferred categories. Multi-category amounts are split equally; Unclassified remains available as a neutral category.</p></details>
          </> : <div className="data-notice data-notice-warning">No programme allocation is available for the selected filters.</div>}
        </section>
      </div>

      {drawerOpen && <div className="overview-filter-backdrop" onMouseDown={closeDrawer}><aside id="overview-filter-drawer" className="overview-filter-drawer" ref={drawerRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Global grant filters" onMouseDown={event => event.stopPropagation()}><div className="overview-filter-drawer-header"><div><span><SlidersHorizontal size={15} /> Global grant filters</span><h2>Filter grant analysis</h2></div><button type="button" aria-label="Close filters" onClick={closeDrawer}><X size={18} /></button></div><div className="overview-filter-drawer-body">
        <label><span>Period</span><select value={draft.periodPreset} onChange={event => setDraft(applyPreset(event.target.value as PeriodPreset))}><option value="all">All available data</option><option value="last12">Last 12 months</option><option value="last24">Last 24 months</option><option value="currentYear">Current calendar year</option><option value="custom">Custom range</option></select></label>
        {draft.periodPreset === "custom" && <div className="filter-date-grid"><label><span>From</span><input type="date" min={payload?.available_date_range.from || undefined} max={payload?.available_date_range.to || undefined} value={draft.dateFrom} onChange={event => setDraft(current => ({ ...current, dateFrom: event.target.value }))} /></label><label><span>To</span><input type="date" min={draft.dateFrom || payload?.available_date_range.from || undefined} max={payload?.available_date_range.to || undefined} value={draft.dateTo} onChange={event => setDraft(current => ({ ...current, dateTo: event.target.value }))} /></label></div>}
        <label><span>Currency</span><select value={draft.currency} onChange={event => setDraft(current => ({ ...current, currency: event.target.value }))}><option value="">Auto · EUR converted</option>{(payload?.trends.available_currencies || []).map(currency => <option key={currency} value={currency}>{currency} · original only</option>)}</select></label>
        <label><span>Time granularity</span><select value={draft.granularity} onChange={event => setDraft(current => ({ ...current, granularity: event.target.value as Granularity }))}><option value="auto">Auto</option><option value="monthly">Monthly</option><option value="yearly">Yearly</option></select></label>
        <fieldset><legend>Beneficiary geography</legend><div className="filter-checklist">{beneficiaryOptions.map(value => <label key={value}><input type="checkbox" checked={draft.beneficiaryGeographies.includes(value)} onChange={event => setDraft(current => ({ ...current, beneficiaryGeographies: event.target.checked ? [...current.beneficiaryGeographies, value] : current.beneficiaryGeographies.filter(item => item !== value) }))} />{value}</label>)}</div></fieldset>
        <fieldset><legend>Programme area</legend><div className="filter-checklist">{PROGRAMMES.map(value => <label key={value}><input type="checkbox" checked={draft.programmeAreas.includes(value)} onChange={event => setDraft(current => ({ ...current, programmeAreas: event.target.checked ? [...current.programmeAreas, value] : current.programmeAreas.filter(item => item !== value) }))} />{value}</label>)}</div></fieldset>
        <label><span>Donor</span><input value={draft.donor} placeholder="Name contains…" onChange={event => setDraft(current => ({ ...current, donor: event.target.value }))} /></label>
        <label><span>Recipient</span><input value={draft.recipient} placeholder="Name contains…" onChange={event => setDraft(current => ({ ...current, recipient: event.target.value }))} /></label>
      </div><div className="overview-filter-drawer-footer"><button type="button" onClick={() => setDraft(EMPTY_FILTERS)}>Reset</button><button type="button" className="btn btn-primary" onClick={applyFilters}>Apply filters <ChevronRight size={15} /></button></div></aside></div>}
    </div>
  );
}
