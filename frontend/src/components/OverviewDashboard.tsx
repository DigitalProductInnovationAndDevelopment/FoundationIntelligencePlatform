import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Building2, CalendarRange, ChevronRight, ExternalLink, LoaderCircle, Search, SlidersHorizontal, Star, X } from "lucide-react";
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
import type {
  GrantMapFilters,
  GrantMapResponse,
  MapCountrySelection,
  SourceFunderCountrySelection,
} from "./GrantWorldMap";
import {
  applyGrantScopeToParams,
  grantScopeChips,
  grantScopeFromUrl,
  grantScopeToApiParams,
  type GrantScope,
} from "../lib/grantScope";

const GrantWorldMap = lazy(() => import("./GrantWorldMap"));

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

interface MapConnectionsPayload {
  status: string;
  connections: GrantMapResponse["connections"];
  connection_grant_count: number;
  selected_currency: string | null;
}

interface EntitySuggestion {
  name: string;
  grant_count: number;
}

interface EntitySuggestionResponse {
  status: string;
  donors: EntitySuggestion[];
  recipients: EntitySuggestion[];
}

type DrilldownSelection = { type: "period" | "programme_area"; value: string };
type DrilldownTab = "funders" | "recipients" | "grants";
export type FavoriteGrantExplorerPayload = {
  key: string;
  label: string;
  route: string;
  selection: DrilldownSelection;
  savedAt: number;
};
type DrilldownProfile = { id: number; name: string } | null;
interface DrilldownEntity {
  funder_key?: string;
  recipient_key?: string;
  name: string;
  grant_count: number;
  funding_total: number | null;
  currency: string | null;
  profile: DrilldownProfile;
}
interface DrilldownGrant {
  grant_id: string;
  award_date: string | null;
  funder_name: string;
  recipient_name: string;
  amount: number | null;
  currency: string | null;
  original_amount: number | null;
  original_currency: string | null;
  description: string | null;
  evidence_links: Array<{ label: string; link_type: "website" | "json" | string; url: string }>;
}
interface DrilldownResponse {
  status: string;
  selection: { type: "period" | "programme_area"; value: string; label: string };
  summary: {
    grant_count: number;
    funding_total: number | null;
    currency: string | null;
    funder_count: number;
    recipient_count: number;
    country_count: number;
    amount_excluded_grant_count: number;
  };
  funders: DrilldownEntity[];
  recipients: DrilldownEntity[];
  countries: Array<{ country_code: string; country_name: string; grant_count: number }>;
  grants: DrilldownGrant[];
}

// A saved exploration is normally reopened during the same application session.
// Keeping its already-resolved result here makes that transition immediate, while
// the server-side cache still covers a browser refresh or a later return visit.
const drilldownResponseCache = new Map<string, DrilldownResponse>();
const DRILLDOWN_RESPONSE_CACHE_LIMIT = 24;
const overviewResponseCache = new Map<string, { payload: OverviewPayload; cachedAt: number }>();
const OVERVIEW_RESPONSE_CACHE_LIMIT = 24;
// The server maintains a persisted overview cache keyed by the source-data
// revision. Keep the browser cache short so an enrichment/pipeline publish is
// visible promptly even when it finishes while this tab is open.
const OVERVIEW_RESPONSE_CACHE_TTL_MS = 15 * 1000;
const ORGANIZATION_ONLY_SOURCES = new Set([
  "Charity Commission for England and Wales",
  "Philea",
]);

function rememberDrilldownResponse(key: string, response: DrilldownResponse) {
  drilldownResponseCache.delete(key);
  drilldownResponseCache.set(key, response);
  if (drilldownResponseCache.size > DRILLDOWN_RESPONSE_CACHE_LIMIT) {
    const oldestKey = drilldownResponseCache.keys().next().value;
    if (oldestKey !== undefined) drilldownResponseCache.delete(oldestKey);
  }
}

function rememberOverviewResponse(key: string, payload: OverviewPayload) {
  // A no-data response is often a transient result while a pipeline has just
  // published a new database or while a user changes the scope. Do not make
  // that empty state sticky in the browser for a later return to the map.
  if (payload.map.status !== "available") return;
  overviewResponseCache.delete(key);
  overviewResponseCache.set(key, { payload, cachedAt: Date.now() });
  if (overviewResponseCache.size > OVERVIEW_RESPONSE_CACHE_LIMIT) {
    const oldestKey = overviewResponseCache.keys().next().value;
    if (oldestKey !== undefined) overviewResponseCache.delete(oldestKey);
  }
}

interface Props {
  apiBase: string;
  online: boolean;
  selectedSources: string[];
  onOpenOrganizationDirectory: (filters: GrantMapFilters) => void;
  onOpenProfile: (profileId: number, profileName: string) => void;
  onSearchOrganization: (organizationName: string) => void;
  onExploreSourceFunders: (selection: SourceFunderCountrySelection, filters: OverviewFilters) => void;
  favoriteGrantExplorerKeys?: string[];
  onToggleFavoriteGrantExplorer?: (favorite: FavoriteGrantExplorerPayload) => void;
  presentation?: "default" | "favorite-explorer";
  initialDrilldown?: DrilldownSelection | null;
  onBackToFavorites?: () => void;
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

function matchingEntitySuggestions(items: EntitySuggestion[], value: string): EntitySuggestion[] {
  const query = value.trim().toLocaleLowerCase();
  if (!query) return [];
  const startsWith: EntitySuggestion[] = [];
  const contains: EntitySuggestion[] = [];
  for (const item of items) {
    const name = item.name.toLocaleLowerCase();
    if (name.startsWith(query)) startsWith.push(item);
    else if (name.includes(query)) contains.push(item);
    if (startsWith.length + contains.length >= 7) break;
  }
  return [...startsWith, ...contains].slice(0, 7);
}

function EntitySuggestionInput({
  id,
  label,
  value,
  suggestions,
  loading,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  suggestions: EntitySuggestion[];
  loading: boolean;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const matches = useMemo(() => matchingEntitySuggestions(suggestions, value), [suggestions, value]);
  const showSuggestions = open && value.trim().length > 0;
  const listId = `${id}-suggestions`;

  return (
    <div className="overview-entity-autocomplete">
      <label htmlFor={id}><span>{label}</span></label>
      <div className="overview-entity-input-wrap">
        <input
          id={id}
          value={value}
          placeholder="Name contains…"
          autoComplete="off"
          aria-autocomplete="list"
          aria-expanded={showSuggestions}
          aria-controls={showSuggestions ? listId : undefined}
          onFocus={() => setOpen(true)}
          onBlur={() => window.setTimeout(() => setOpen(false), 120)}
          onKeyDown={event => {
            if (event.key === "Escape") setOpen(false);
          }}
          onChange={event => {
            onChange(event.target.value);
            setOpen(true);
          }}
        />
        {showSuggestions && (
          <div id={listId} className="overview-entity-suggestions" role="listbox" aria-label={`${label} suggestions`}>
            {loading ? (
              <span>Loading cached names…</span>
            ) : matches.length ? matches.map(item => (
              <button
                key={`${item.name}:${item.grant_count}`}
                type="button"
                role="option"
                onMouseDown={event => event.preventDefault()}
                onClick={() => {
                  onChange(item.name);
                  setOpen(false);
                }}
              >
                <strong>{item.name}</strong>
                <small>{item.grant_count.toLocaleString("en-GB")} observed grants</small>
              </button>
            )) : (
              <span>No cached matches yet.</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function OverviewDashboard({ apiBase, online, selectedSources, onOpenOrganizationDirectory, onOpenProfile, onSearchOrganization, onExploreSourceFunders, favoriteGrantExplorerKeys = [], onToggleFavoriteGrantExplorer, presentation = "default", initialDrilldown = null, onBackToFavorites }: Props) {
  const [filters, setFilters] = useState<OverviewFilters>(filtersFromUrl);
  const [draft, setDraft] = useState<OverviewFilters>(filters);
  const [payload, setPayload] = useState<OverviewPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [showAllProgrammes, setShowAllProgrammes] = useState(false);
  const [includeUnclassified, setIncludeUnclassified] = useState(false);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [selectedMapCountryCode, setSelectedMapCountryCode] = useState<string | null>(null);
  const [includeConnections, setIncludeConnections] = useState(false);
  const [mapConnections, setMapConnections] = useState<MapConnectionsPayload | null>(null);
  const [mapConnectionsLoading, setMapConnectionsLoading] = useState(false);
  const [mapConnectionsError, setMapConnectionsError] = useState<string | null>(null);
  const [trendPeriodOpen, setTrendPeriodOpen] = useState(false);
  const [trendDateFrom, setTrendDateFrom] = useState(filters.dateFrom);
  const [trendDateTo, setTrendDateTo] = useState(filters.dateTo);
  const [trendOverride, setTrendOverride] = useState<OverviewPayload["trends"] | null>(null);
  const [trendLoading, setTrendLoading] = useState(false);
  const [drilldownSelection, setDrilldownSelection] = useState<DrilldownSelection | null>(initialDrilldown);
  const [drilldown, setDrilldown] = useState<DrilldownResponse | null>(null);
  const [drilldownTab, setDrilldownTab] = useState<DrilldownTab>("funders");
  const [drilldownLoading, setDrilldownLoading] = useState(false);
  const [drilldownError, setDrilldownError] = useState<string | null>(null);
  const [entitySuggestions, setEntitySuggestions] = useState<EntitySuggestionResponse>({ status: "idle", donors: [], recipients: [] });
  const [entitySuggestionsLoading, setEntitySuggestionsLoading] = useState(false);
  const requestVersion = useRef(0);
  const trendRequestVersion = useRef(0);
  const drilldownRequestVersion = useRef(0);
  const mapConnectionsRequestVersion = useRef(0);
  const handledRefreshNonce = useRef(refreshNonce);
  const drawerRef = useRef<HTMLElement>(null);
  const drawerTriggerRef = useRef<HTMLElement | null>(null);
  const entitySuggestionCache = useRef(new Map<string, EntitySuggestionResponse>());
  const overviewSourcesKey = selectedSources
    .filter(source => !ORGANIZATION_ONLY_SOURCES.has(source.trim()))
    .map(source => source.trim()).filter(Boolean).sort().join("\u001f");
  const overviewSources = useMemo(
    () => overviewSourcesKey.split("\u001f").filter(Boolean),
    [overviewSourcesKey],
  );
  const entitySuggestionSourceKey = useMemo(
    () => overviewSourcesKey,
    [overviewSourcesKey],
  );
  const activeFilterCount = Number(Boolean(filters.currency)) + Number(Boolean(filters.dateFrom || filters.dateTo))
    + filters.beneficiaryGeographies.length + filters.programmeAreas.length
    + Number(Boolean(filters.donor.trim())) + Number(Boolean(filters.recipient.trim()))
    + Number(filters.granularity !== "auto");
  const connectionsDisabledReason = (
    filters.dateFrom
    || filters.dateTo
    || filters.beneficiaryGeographies.length
    || filters.programmeAreas.length
    || filters.donor.trim()
    || filters.recipient.trim()
  ) ? "Connections are available only for the unfiltered map or a currency-only scope." : null;

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
    if (!online || presentation === "favorite-explorer") return;
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
      sources: overviewSources,
    };
    const params = grantScopeToApiParams(requestScope);
    params.set("granularity", "auto");
    const requestUrl = `${apiBase}/api/charities/grants/overview?${params.toString()}`;
    const forceRefresh = handledRefreshNonce.current !== refreshNonce;
    handledRefreshNonce.current = refreshNonce;
    const cachedOverview = overviewResponseCache.get(requestUrl);
    if (!forceRefresh && cachedOverview && Date.now() - cachedOverview.cachedAt < OVERVIEW_RESPONSE_CACHE_TTL_MS) {
      setPayload(cachedOverview.payload);
      setError(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    fetch(requestUrl, { credentials: "include", signal: controller.signal })
      .then(async response => {
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || `Overview request failed (${response.status}).`);
        return result as OverviewPayload;
      })
      .then(result => {
        if (currentVersion !== requestVersion.current) return;
        rememberOverviewResponse(requestUrl, result);
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
  }, [apiBase, online, overviewRequestFilters, overviewSources, presentation, refreshNonce]);

  useEffect(() => {
    if (connectionsDisabledReason && includeConnections) setIncludeConnections(false);
  }, [connectionsDisabledReason, includeConnections]);

  useEffect(() => {
    if (!includeConnections || !online || connectionsDisabledReason) {
      setMapConnectionsLoading(false);
      setMapConnectionsError(null);
      return;
    }
    const controller = new AbortController();
    const currentVersion = ++mapConnectionsRequestVersion.current;
    const params = new URLSearchParams({ limit: "250" });
    if (filters.currency) params.set("currency", filters.currency);
    setMapConnectionsLoading(true);
    setMapConnections(null);
    setMapConnectionsError(null);
    fetch(`${apiBase}/api/charities/grants/map/connections?${params.toString()}`, {
      credentials: "include",
      signal: controller.signal,
    })
      .then(async response => {
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || `Map connections failed (${response.status}).`);
        return result as MapConnectionsPayload;
      })
      .then(result => {
        if (currentVersion !== mapConnectionsRequestVersion.current) return;
        setMapConnections(result);
      })
      .catch(requestError => {
        if ((requestError as Error).name !== "AbortError" && currentVersion === mapConnectionsRequestVersion.current) {
          setMapConnections(null);
          setMapConnectionsError((requestError as Error).message || "Country connections are temporarily unavailable.");
        }
      })
      .finally(() => {
        if (currentVersion === mapConnectionsRequestVersion.current) setMapConnectionsLoading(false);
      });
    return () => controller.abort();
  }, [apiBase, connectionsDisabledReason, filters.currency, includeConnections, online]);

  const mapPayload = useMemo<GrantMapResponse>(() => {
    const base = payload?.map || EMPTY_MAP;
    if (!includeConnections || !mapConnections) {
      return { ...base, connections: [], connection_grant_count: 0 };
    }
    return {
      ...base,
      connections: mapConnections.connections,
      connection_grant_count: mapConnections.connection_grant_count,
    };
  }, [includeConnections, mapConnections, payload?.map]);

  useEffect(() => {
    if (!online || presentation === "favorite-explorer" || filters.granularity === "auto") {
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
      sources: overviewSources,
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
  }, [apiBase, filters, online, overviewSources, presentation]);

  useEffect(() => {
    if (!drilldownSelection) return;
    if (!online) {
      setDrilldown(null);
      setDrilldownError("Observed grant details require the connected data service.");
      return;
    }
    const currentVersion = ++drilldownRequestVersion.current;
    const params = grantScopeToApiParams({
      currency: filters.currency || undefined,
      dateFrom: filters.dateFrom || undefined,
      dateTo: filters.dateTo || undefined,
      beneficiaryGeographies: filters.beneficiaryGeographies,
      programmeAreas: filters.programmeAreas,
      donor: filters.donor,
      recipient: filters.recipient,
      sources: overviewSources,
    });
    params.set("selection_type", drilldownSelection.type);
    params.set("selection_value", drilldownSelection.value);
    const cacheKey = params.toString();
    const cachedResponse = drilldownResponseCache.get(cacheKey);
    if (cachedResponse) {
      setDrilldown(cachedResponse);
      setDrilldownError(null);
      setDrilldownLoading(false);
      return;
    }
    const controller = new AbortController();
    setDrilldownLoading(true);
    setDrilldownError(null);
    fetch(`${apiBase}/api/charities/grants/overview/drilldown?${params.toString()}`, { credentials: "include", signal: controller.signal })
      .then(async response => {
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || `Grant detail request failed (${response.status}).`);
        return result as DrilldownResponse;
      })
      .then(result => {
        if (currentVersion !== drilldownRequestVersion.current) return;
        rememberDrilldownResponse(cacheKey, result);
        setDrilldown(result);
      })
      .catch(requestError => {
        if ((requestError as Error).name !== "AbortError" && currentVersion === drilldownRequestVersion.current) {
          setDrilldownError((requestError as Error).message);
          setDrilldown(null);
        }
      })
      .finally(() => {
        if (currentVersion === drilldownRequestVersion.current) setDrilldownLoading(false);
      });
    return () => controller.abort();
  }, [apiBase, drilldownSelection, filters, online, overviewSources]);

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

  useEffect(() => {
    if (!drawerOpen || !online) return;
    const cached = entitySuggestionCache.current.get(entitySuggestionSourceKey);
    if (cached) {
      setEntitySuggestions(cached);
      return;
    }
    const controller = new AbortController();
    const params = new URLSearchParams({ limit: "2500" });
    params.set("sources", entitySuggestionSourceKey.split("\u001f").filter(Boolean).join(","));
    setEntitySuggestionsLoading(true);
    fetch(`${apiBase}/api/charities/grants/overview/entity-suggestions?${params.toString()}`, {
      credentials: "include",
      signal: controller.signal,
    })
      .then(async response => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || `Entity suggestions failed (${response.status}).`);
        return body as EntitySuggestionResponse;
      })
      .then(body => {
        const next = {
          status: body.status || "available",
          donors: Array.isArray(body.donors) ? body.donors : [],
          recipients: Array.isArray(body.recipients) ? body.recipients : [],
        };
        entitySuggestionCache.current.set(entitySuggestionSourceKey, next);
        setEntitySuggestions(next);
      })
      .catch(reason => {
        if ((reason as Error).name !== "AbortError") setEntitySuggestions({ status: "unavailable", donors: [], recipients: [] });
      })
      .finally(() => {
        if (!controller.signal.aborted) setEntitySuggestionsLoading(false);
      });
    return () => controller.abort();
  }, [apiBase, drawerOpen, entitySuggestionSourceKey, online]);

  const closeDrawer = () => {
    setDrawerOpen(false);
  };

  useEffect(() => {
    const openFilters = () => {
      drawerTriggerRef.current = document.activeElement instanceof HTMLElement && document.activeElement !== document.body
        ? document.activeElement
        : document.querySelector<HTMLButtonElement>(".app-header-filter, .header-overview-filter");
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
    const refreshWhenReturningToTab = () => {
      if (document.visibilityState === "visible" && online && presentation === "default") {
        setRefreshNonce(current => current + 1);
      }
    };
    document.addEventListener("visibilitychange", refreshWhenReturningToTab);
    return () => document.removeEventListener("visibilitychange", refreshWhenReturningToTab);
  }, [online, presentation]);

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

  useEffect(() => {
    if (drawerOpen || !drawerTriggerRef.current) return;
    const trigger = drawerTriggerRef.current;
    const frame = window.requestAnimationFrame(() => {
      trigger.focus();
      if (drawerTriggerRef.current === trigger) drawerTriggerRef.current = null;
    });
    return () => window.cancelAnimationFrame(frame);
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

  const openDrilldown = (selection: DrilldownSelection) => {
    setDrilldownSelection(selection);
    setDrilldown(null);
    setDrilldownError(null);
    setDrilldownTab("funders");
  };

  const closeDrilldown = () => {
    if (presentation === "favorite-explorer" && onBackToFavorites) {
      onBackToFavorites();
      return;
    }
    drilldownRequestVersion.current += 1;
    setDrilldownSelection(null);
    setDrilldown(null);
    setDrilldownError(null);
  };

  const eligibleThemeItems = useMemo(
    () => (payload?.themes.items || []).filter(item => includeUnclassified || item.programme_area !== "Unclassified"),
    [includeUnclassified, payload?.themes.items],
  );
  const visibleThemeItems = useMemo(
    () => showAllProgrammes ? eligibleThemeItems : eligibleThemeItems.slice(0, 8),
    [eligibleThemeItems, showAllProgrammes],
  );
  const hiddenThemeCount = Math.max(0, eligibleThemeItems.length - visibleThemeItems.length);

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
  const drilldownFavorite = useMemo(() => {
    if (!drilldownSelection) return null;
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
    if (filters.granularity !== "auto") params.set("grant_granularity", filters.granularity);
    const label = drilldown?.selection.label || drilldownSelection.value;
    const route = `?${params.toString()}`;
    return {
      key: `grant-explorer:${drilldownSelection.type}:${drilldownSelection.value}:${params.toString()}`,
      label: `${drilldownSelection.type === "period" ? "Grant period" : "Programme area"} · ${label}`,
      route,
      selection: drilldownSelection,
      savedAt: Date.now(),
    } satisfies FavoriteGrantExplorerPayload;
  }, [drilldown?.selection.label, drilldownSelection, filters, selectedSources]);
  const drilldownIsFavorite = Boolean(drilldownFavorite && favoriteGrantExplorerKeys.includes(drilldownFavorite.key));
  return (
    <div className={`overview-dashboard${presentation === "favorite-explorer" ? " favorite-explorer-dashboard" : ""}`}>
      <h2 className="visually-hidden">Funding Landscape overview</h2>
      {presentation !== "favorite-explorer" && <>
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

      <Suspense fallback={<section className="glass-card route-loading" aria-label="Loading world map"><LoaderCircle className="spin" size={22} /> Loading world map…</section>}>
        <GrantWorldMap
          data={mapPayload}
          loading={loading && !payload}
          error={null}
          filters={legacyMapFilters}
          onOpenOrganizationDirectory={onOpenOrganizationDirectory}
          onExploreSourceFunders={(selection) => onExploreSourceFunders(selection, filters)}
          selectedCountryCode={selectedMapCountryCode}
          onCountrySelectionChange={selectMapCountry}
          refreshing={loading && Boolean(payload)}
          connectionsVisible={includeConnections}
          connectionsLoading={mapConnectionsLoading}
          connectionsError={mapConnectionsError}
          connectionsDisabledReason={connectionsDisabledReason}
          onConnectionsVisibilityChange={setIncludeConnections}
          onResetScope={() => window.dispatchEvent(new Event("overview-reset-filters"))}
        />
      </Suspense>

      <div className="analytics-charts-grid overview-analytics-grid">
        <section className="glass-card analytics-chart-card compact-chart" aria-labelledby="grant-trend-title">
          <div className="chart-card-header trend-card-header">
            <div><h3 id="grant-trend-title">Grant Awards Over Time</h3><span>{trends?.granularity === "yearly" ? "Annual view" : "Monthly view"} · {chartPeriod} · {filters.currency ? `${trends?.currency || filters.currency} original` : "EUR · Auto (ECB converted)"}</span></div>
            <div className="trend-controls">
              <div className="chart-segmented trend-segmented" role="group" aria-label="Grant trend view and period"><button type="button" className={filters.granularity === "auto" ? "active" : ""} onClick={() => setFilters(current => ({ ...current, granularity: "auto" }))}>Auto</button><button type="button" className={filters.granularity === "monthly" ? "active" : ""} onClick={() => setFilters(current => ({ ...current, granularity: "monthly" }))}>Monthly</button><button type="button" className={filters.granularity === "yearly" ? "active" : ""} onClick={() => setFilters(current => ({ ...current, granularity: "yearly" }))}>Yearly</button><button type="button" className={`trend-custom-trigger${filters.periodPreset === "custom" ? " active" : ""}`} aria-expanded={trendPeriodOpen} onClick={openTrendCustomPeriod}><CalendarRange size={14} /> Custom period</button></div>
              {trendPeriodOpen && <section className="trend-period-picker" aria-label="Custom grant award period">
                <div className="trend-period-copy"><strong>Custom period</strong><span>Choose the award-date range used across this dashboard.</span></div>
                <div className="trend-period-fields"><label><span>From</span><input type="date" min={payload?.available_date_range.from || undefined} max={payload?.available_date_range.to || undefined} value={trendDateFrom} onChange={event => setTrendDateFrom(event.target.value)} /></label><label><span>To</span><input type="date" min={trendDateFrom || payload?.available_date_range.from || undefined} max={payload?.available_date_range.to || undefined} value={trendDateTo} onChange={event => setTrendDateTo(event.target.value)} /></label></div>
                <div className="trend-period-actions"><button type="button" onClick={() => setTrendPeriodOpen(false)}>Cancel</button><button type="button" className="btn btn-primary" onClick={applyTrendCustomPeriod}>Apply period</button></div>
              </section>}
            </div>
          </div>
          {trendIsLoading && !trends ? <TrendLoadingState /> : trends?.status === "available" && trends.items.length ? <>
            <p className="visually-hidden">Grant awards are shown for {trends.items.length} {trends.granularity} periods in {trends.currency}. Use the chart tooltip to inspect total awarded funding, grant count, and mapped versus unmapped grants for each period.</p>
            <div className={`analytics-chart-plot compact trend-chart-plot${trendIsLoading ? " is-refreshing" : ""}`}><ResponsiveContainer width="100%" height="100%"><BarChart data={trends.items} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}><CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,.09)" /><XAxis dataKey="month" minTickGap={28} tick={{ fill: "#707070", fontSize: 10 }} tickFormatter={value => String(value).slice(2)} /><YAxis width={58} tick={{ fill: "#707070", fontSize: 10 }} tickFormatter={value => formatCurrency(Number(value), trends.currency).replace("£", "£")} /><Tooltip content={({ active, payload: rows, label }) => { const item = rows?.[0]?.payload as TrendItem | undefined; if (!active || !item) return null; return <div className="chart-tooltip"><strong>{label}</strong>{item.coverage_status === "observed" ? <><span>{formatCurrency(item.total_amount, trends.currency)}</span><span>{item.grant_count} grants · {item.mapped_grant_count} mapped · {item.unmapped_grant_count} unmapped</span><small>Click to explore this period.</small></> : <span>{item.coverage_status === "partial" ? "Source records without a valid aggregate." : "No source coverage established; not a confirmed zero."}</span>}</div>; }} /><Bar dataKey="total_amount" name="Awarded funding" fill="var(--nl-chart-primary)" radius={[4, 4, 0, 0]} isAnimationActive={false} cursor="pointer" onClick={data => { const item = data?.payload as TrendItem | undefined; if (item?.grant_count) openDrilldown({ type: "period", value: item.month }); }} /></BarChart></ResponsiveContainer>{trendIsLoading && <div className="trend-chart-refresh"><TrendLoadingState compact /></div>}</div>
            <details className="chart-methodology"><summary>Methodology and data coverage</summary><p>Award-date aggregation from the filtered 360Giving grant population. Auto uses the stored ECB reference rate for the award date, or the preceding ECB business day; empty periods are never shown as zero funding.</p></details>
          </> : <div className="data-notice data-notice-warning">No qualifying grant awards are available for the selected filters.</div>}
        </section>

        <section className="glass-card analytics-chart-card compact-chart" aria-labelledby="programme-chart-title">
          <div className="chart-card-header programme-card-header"><div><h3 id="programme-chart-title">Grant Allocation by Programme Area</h3><span>{themes?.classification_coverage.classified_percentage ?? "—"}% classification coverage · {themes?.classification_coverage.classified_grant_count ?? 0} classified grants</span></div><div className="programme-controls"><div className="chart-segmented" role="group" aria-label="Classification scope"><button type="button" className={!includeUnclassified ? "active" : ""} aria-pressed={!includeUnclassified} onClick={() => setIncludeUnclassified(false)}>Classified</button><button type="button" className={includeUnclassified ? "active" : ""} aria-pressed={includeUnclassified} onClick={() => setIncludeUnclassified(true)}>All grants</button></div><div className="chart-segmented" role="group" aria-label="Programme category count"><button type="button" className={!showAllProgrammes ? "active" : ""} aria-pressed={!showAllProgrammes} onClick={() => setShowAllProgrammes(false)}>Top 8</button><button type="button" className={showAllProgrammes ? "active" : ""} aria-pressed={showAllProgrammes} onClick={() => setShowAllProgrammes(true)}>All categories</button></div></div></div>
          {loading && !themes ? <div className="chart-loading"><LoaderCircle size={20} /> Loading programme allocation…</div> : themes?.status === "available" && visibleThemeItems.length ? <>
            <p className="visually-hidden">Programme allocation is shown across {visibleThemeItems.length} categories in {themes.currency}. Programme classification coverage is {themes.classification_coverage.classified_percentage} percent.</p>
            <div className="analytics-chart-plot compact programme-chart-plot"><ResponsiveContainer width="100%" height="100%"><BarChart data={visibleThemeItems} layout="vertical" margin={{ top: 0, right: 10, bottom: 0, left: 20 }}><CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,.09)" /><XAxis type="number" tick={{ fill: "#707070", fontSize: 10 }} tickFormatter={value => formatCurrency(Number(value), themes.currency).replace("£", "£")} /><YAxis type="category" width={158} dataKey="programme_area" tick={{ fill: "#3f3f3f", fontSize: 10 }} /><Tooltip formatter={(value) => formatCurrency(Number(value), themes.currency)} /><Bar dataKey="allocated_amount" radius={[0, 4, 4, 0]} isAnimationActive={false}>{visibleThemeItems.map(item => <Cell key={item.programme_area} fill={item.programme_area === "Unclassified" ? "#a6a6a6" : "#a29aff"} cursor="pointer" onClick={() => openDrilldown({ type: "programme_area", value: item.programme_area })} />)}</Bar></BarChart></ResponsiveContainer></div>
            {(hiddenThemeCount > 0 || (!includeUnclassified && themes.classification_coverage.unclassified_grant_count > 0)) && <p className="chart-coverage-note">{hiddenThemeCount > 0 ? `Showing the top 8 of ${eligibleThemeItems.length} categories.` : ""}{hiddenThemeCount > 0 && !includeUnclassified && themes.classification_coverage.unclassified_grant_count > 0 ? " " : ""}{!includeUnclassified && themes.classification_coverage.unclassified_grant_count > 0 ? `${themes.classification_coverage.unclassified_grant_count} unclassified grants are excluded.` : ""}</p>}
            <details className="chart-methodology"><summary>Methodology and data coverage</summary><p>Source categories take precedence over accepted inferred categories. Multi-category amounts are split equally; Unclassified remains available as a neutral category.</p></details>
          </> : <div className="data-notice data-notice-warning">No programme allocation is available for the selected filters.</div>}
        </section>
      </div>
      </>}

      {drilldownSelection && <div className={`overview-drilldown-backdrop${presentation === "favorite-explorer" ? " is-favorite-explorer" : ""}`} onMouseDown={presentation === "favorite-explorer" ? undefined : closeDrilldown}>
        <aside className="overview-drilldown" role="dialog" aria-modal={presentation !== "favorite-explorer" || undefined} aria-label="Observed grant explorer" onMouseDown={event => event.stopPropagation()}>
          <div className="overview-drilldown-header">
            <div><span>Observed grant explorer</span><h2>{drilldown?.selection.label || drilldownSelection.value}</h2><p>{drilldownSelection.type === "period" ? "Grants awarded in this selected period." : "Grants classified in this selected programme area."}</p></div>
            <div className="overview-drilldown-actions">
              {presentation === "favorite-explorer" && <button type="button" className="overview-drilldown-back" onClick={closeDrilldown}><ArrowLeft size={15} /> Favorites</button>}
              {drilldownFavorite && onToggleFavoriteGrantExplorer && <button type="button" className={`favorite-toggle${drilldownIsFavorite ? " is-favorite" : ""}`} aria-label={`${drilldownIsFavorite ? "Remove" : "Save"} this grant explorer from favorites`} aria-pressed={drilldownIsFavorite} onClick={() => onToggleFavoriteGrantExplorer(drilldownFavorite)}><Star size={16} fill={drilldownIsFavorite ? "currentColor" : "none"} /></button>}
              <button type="button" onClick={closeDrilldown} aria-label="Close observed grant explorer"><X size={18} /></button>
            </div>
          </div>
          {drilldownLoading ? <div className="overview-drilldown-loading"><LoaderCircle size={22} /><div><strong>Preparing observed grant detail</strong><span>Ranking funders, recipients, and source grants for this selection.</span></div></div>
            : drilldownError ? <div className="data-notice data-notice-warning">{drilldownError}</div>
              : drilldown?.status === "available" ? <>
                <section className="overview-drilldown-summary" aria-label="Selected grant summary">
                  <div><span>Observed funding</span><strong>{formatCurrency(drilldown.summary.funding_total, drilldown.summary.currency)}</strong></div>
                  <div><span>Grants</span><strong>{drilldown.summary.grant_count.toLocaleString("en-GB")}</strong></div>
                  <div><span>Funders</span><strong>{drilldown.summary.funder_count.toLocaleString("en-GB")}</strong></div>
                  <div><span>Recipients</span><strong>{drilldown.summary.recipient_count.toLocaleString("en-GB")}</strong></div>
                </section>
                <div className="overview-drilldown-countries"><span>Beneficiary geography</span><div>{drilldown.countries.length ? drilldown.countries.map(country => <em key={country.country_code || country.country_name}>{country.country_name} · {country.grant_count}</em>) : <small>No mapped beneficiary geography in this selection.</small>}</div></div>
                {drilldown.summary.amount_excluded_grant_count > 0 && <p className="overview-drilldown-note">{drilldown.summary.amount_excluded_grant_count.toLocaleString("en-GB")} grant{drilldown.summary.amount_excluded_grant_count === 1 ? "" : "s"} have no usable {drilldown.summary.currency || "selected"} amount and are excluded from the funding total.</p>}
                <div className="overview-drilldown-tabs" role="tablist" aria-label="Observed grant detail">
                  <button type="button" role="tab" aria-selected={drilldownTab === "funders"} className={drilldownTab === "funders" ? "active" : ""} onClick={() => setDrilldownTab("funders")}>Funders <span>{drilldown.summary.funder_count}</span></button>
                  <button type="button" role="tab" aria-selected={drilldownTab === "recipients"} className={drilldownTab === "recipients" ? "active" : ""} onClick={() => setDrilldownTab("recipients")}>Recipients <span>{drilldown.summary.recipient_count}</span></button>
                  <button type="button" role="tab" aria-selected={drilldownTab === "grants"} className={drilldownTab === "grants" ? "active" : ""} onClick={() => setDrilldownTab("grants")}>Grants <span>{drilldown.summary.grant_count}</span></button>
                </div>
                {drilldownTab === "funders" && <div className="overview-drilldown-list">{drilldown.funders.map(funder => <article key={funder.funder_key || funder.name}><div><strong>{funder.name}</strong><small>{funder.grant_count.toLocaleString("en-GB")} grants · {formatCurrency(funder.funding_total, funder.currency)}</small></div>{funder.profile ? <button type="button" className="btn btn-secondary" onClick={() => { closeDrilldown(); onOpenProfile(funder.profile!.id, funder.profile!.name); }}><Building2 size={14} /> Open profile</button> : <button type="button" className="btn btn-secondary" onClick={() => { closeDrilldown(); onSearchOrganization(funder.name); }}><Search size={14} /> Search research</button>}</article>)}</div>}
                {drilldownTab === "recipients" && <div className="overview-drilldown-list">{drilldown.recipients.map(recipientItem => <article key={recipientItem.recipient_key || recipientItem.name}><div><strong>{recipientItem.name}</strong><small>{recipientItem.grant_count.toLocaleString("en-GB")} grants · {formatCurrency(recipientItem.funding_total, recipientItem.currency)}</small></div>{recipientItem.profile ? <button type="button" className="btn btn-secondary" onClick={() => { closeDrilldown(); onOpenProfile(recipientItem.profile!.id, recipientItem.profile!.name); }}><Building2 size={14} /> Open profile</button> : <button type="button" className="btn btn-secondary" onClick={() => { closeDrilldown(); onSearchOrganization(recipientItem.name); }}><Search size={14} /> Search research</button>}</article>)}</div>}
                {drilldownTab === "grants" && <div className="overview-drilldown-list overview-drilldown-grants">{drilldown.grants.map(grant => <article key={grant.grant_id}><div><strong>{grant.recipient_name}</strong><small>{grant.award_date || "Undated"} · {formatCurrency(grant.amount, grant.currency)} · from {grant.funder_name}</small>{grant.description && <p>{grant.description}</p>}</div><div className="overview-drilldown-evidence">{grant.evidence_links.slice(0, 2).map(link => <a key={`${link.label}-${link.url}`} href={link.url} target="_blank" rel="noreferrer"><span>{link.link_type === "json" ? "JSON" : "Website"}</span><ExternalLink size={13} /></a>)}</div></article>)}</div>}
              </> : <div className="data-notice data-notice-warning">No qualifying grants are available for this selection.</div>}
        </aside>
      </div>}

      {drawerOpen && <div className="overview-filter-backdrop" onMouseDown={closeDrawer}><aside id="overview-filter-drawer" className="overview-filter-drawer" ref={drawerRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Global grant filters" onMouseDown={event => event.stopPropagation()}><div className="overview-filter-drawer-header"><div><span><SlidersHorizontal size={15} /> Global grant filters</span><h2>Filter grant analysis</h2></div><button type="button" aria-label="Close filters" onClick={closeDrawer}><X size={18} /></button></div><div className="overview-filter-drawer-body">
        <label><span>Period</span><select value={draft.periodPreset} onChange={event => setDraft(applyPreset(event.target.value as PeriodPreset))}><option value="all">All available data</option><option value="last12">Last 12 months</option><option value="last24">Last 24 months</option><option value="currentYear">Current calendar year</option><option value="custom">Custom range</option></select></label>
        {draft.periodPreset === "custom" && <div className="filter-date-grid"><label><span>From</span><input type="date" min={payload?.available_date_range.from || undefined} max={payload?.available_date_range.to || undefined} value={draft.dateFrom} onChange={event => setDraft(current => ({ ...current, dateFrom: event.target.value }))} /></label><label><span>To</span><input type="date" min={draft.dateFrom || payload?.available_date_range.from || undefined} max={payload?.available_date_range.to || undefined} value={draft.dateTo} onChange={event => setDraft(current => ({ ...current, dateTo: event.target.value }))} /></label></div>}
        <label><span>Currency</span><select value={draft.currency} onChange={event => setDraft(current => ({ ...current, currency: event.target.value }))}><option value="">Auto · EUR converted</option>{(payload?.trends.available_currencies || []).map(currency => <option key={currency} value={currency}>{currency} · original only</option>)}</select></label>
        <label><span>Time granularity</span><select value={draft.granularity} onChange={event => setDraft(current => ({ ...current, granularity: event.target.value as Granularity }))}><option value="auto">Auto</option><option value="monthly">Monthly</option><option value="yearly">Yearly</option></select></label>
        <fieldset><legend>Beneficiary geography</legend><div className="filter-checklist">{beneficiaryOptions.map(value => <label key={value}><input type="checkbox" checked={draft.beneficiaryGeographies.includes(value)} onChange={event => setDraft(current => ({ ...current, beneficiaryGeographies: event.target.checked ? [...current.beneficiaryGeographies, value] : current.beneficiaryGeographies.filter(item => item !== value) }))} />{value}</label>)}</div></fieldset>
        <fieldset><legend>Programme area</legend><div className="filter-checklist">{PROGRAMMES.map(value => <label key={value}><input type="checkbox" checked={draft.programmeAreas.includes(value)} onChange={event => setDraft(current => ({ ...current, programmeAreas: event.target.checked ? [...current.programmeAreas, value] : current.programmeAreas.filter(item => item !== value) }))} />{value}</label>)}</div></fieldset>
        <EntitySuggestionInput id="overview-donor-filter" label="Donor" value={draft.donor} suggestions={entitySuggestions.donors} loading={entitySuggestionsLoading} onChange={value => setDraft(current => ({ ...current, donor: value }))} />
        <EntitySuggestionInput id="overview-recipient-filter" label="Recipient" value={draft.recipient} suggestions={entitySuggestions.recipients} loading={entitySuggestionsLoading} onChange={value => setDraft(current => ({ ...current, recipient: value }))} />
      </div><div className="overview-filter-drawer-footer"><button type="button" onClick={() => setDraft(EMPTY_FILTERS)}>Reset</button><button type="button" className="btn btn-primary" onClick={applyFilters}>Apply filters <ChevronRight size={15} /></button></div></aside></div>}
    </div>
  );
}
