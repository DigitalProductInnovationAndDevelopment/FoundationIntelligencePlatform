import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Building2,
  ChevronLeft,
  ChevronRight,
  Copy,
  ExternalLink,
  FileSearch,
  LoaderCircle,
  Search,
  SlidersHorizontal,
  Star,
  X,
} from "lucide-react";
import {
  DEFAULT_DONOR_DIRECTORY_STATE,
  applyDonorDirectoryStateToParams,
  applyGrantScopeToParams,
  donorDirectoryStateFromUrl,
  emptyGrantScope,
  grantScopeChips,
  grantScopeFilterCount,
  grantScopeFromUrl,
  grantScopeToApiParams,
  normalizeGrantScope,
  removeGrantScopeValue,
  type DonorDirectoryState,
  type GrantScope,
} from "../lib/grantScope";

type ProfileLink =
  | { status: "none" }
  | { status: "multiple"; candidate_count: number; candidate_profile_ids?: number[] }
  | {
      status: "single";
      profile_id: number;
      profile_name: string;
      method?: string | null;
      confidence?: number | null;
      website?: string | null;
      source_url?: string | null;
      registry_link?: Record<string, unknown>;
    };

type DonorResult = {
  rank: number;
  kind: "source_funder";
  identity: {
    key: string;
    namespace: string;
    method: "source_id" | "normalized_name_fallback";
    source_organization_id?: string | null;
    normalized_name_fallback?: string | null;
  };
  source_funder_key: string;
  display_name: string;
  evidence_sources: string[];
  profile_link: ProfileLink;
  observed_activity: {
    grant_count: number;
    recipient_count: number;
    latest_grant_date: string | null;
    observed_funding: number | null;
    displayed_currency: string | null;
    programme_areas: Array<{ name: string; count?: number; provenance?: string }>;
  };
  amount_policy: {
    mode: "automatic_eur" | "original_currency";
    converted_grant_count?: number;
    unconverted_grant_count?: number;
    multi_country_amount_excluded?: number | null;
  };
};

export type FavoriteDonorPayload = {
  key: string;
  name: string;
  route: string;
  funding: string;
  grantCount: number;
  recipientCount: number;
  country: string | null;
  savedAt: number;
};

type DonorListResponse = {
  status: string;
  country: { code: string; name: string };
  summary: {
    matching_funder_count: number;
    matching_grant_count: number;
    distinct_recipient_count: number;
    status_counts: { all: number; linked: number; observed_only: number };
    monetary: { display_currency: string | null; included_funding_total: number | null };
  };
  items: DonorResult[];
  pagination: { page: number; page_size: number; total_items: number; total_pages: number };
  available_currencies: string[];
  metadata: { country_amount_policy: string };
};

type EvidenceLink = {
  kind: string;
  label: string;
  role?: string | null;
  url: string;
  origin: string;
};

type DonorDetail = {
  status: string;
  country: { code: string; name: string };
  funder: DonorResult & {
    activity: {
      grant_count: number;
      distinct_recipient_count: number;
      latest_award_date: string | null;
    };
    observed_funding: { amount: number | null; currency: string | null };
  };
  top_recipients: Array<{
    recipient_key: string;
    name: string;
    grant_count: number;
    observed_funding: number;
    currency: string | null;
    latest_award_date: string | null;
  }>;
  grant_sample: Array<{
    grant_id: string;
    recipient_name: string;
    award_date: string | null;
    amount: number | null;
    currency: string | null;
    original_amount: number | null;
    original_currency: string | null;
    description: string | null;
    evidence_links: EvidenceLink[];
  }>;
  source_evidence: EvidenceLink[];
};

export type HeaderContextState = {
  filterCount: number;
  resetDisabled: boolean;
  filtersExpanded: boolean;
};

interface Props {
  apiBase: string;
  online: boolean;
  selectedSources: string[];
  onHeaderStateChange: (state: HeaderContextState) => void;
  onBackToLandscape: (scope: GrantScope) => void;
  onOpenOrganizationResearch: () => void;
  onOpenRegistrySearch: () => void;
  onOpenProfile: (profileId: number, profileName: string) => void;
  favoriteDonorKeys: string[];
  onToggleFavoriteDonor: (donor: FavoriteDonorPayload) => void;
  onCloseFavoriteDonor?: () => void;
}

function routeState(
  selectedSources: string[],
): { scope: GrantScope; directory: DonorDirectoryState } {
  return {
    scope: grantScopeFromUrl(window.location.search, selectedSources),
    directory: donorDirectoryStateFromUrl(window.location.search),
  };
}

function writeRoute(scope: GrantScope, state: DonorDirectoryState, mode: "push" | "replace" = "replace", marker?: Record<string, unknown>) {
  const params = applyGrantScopeToParams(
    new URLSearchParams(window.location.search),
    scope,
    { persistEmptySources: true },
  );
  applyDonorDirectoryStateToParams(params, state);
  const suffix = params.toString();
  window.history[mode === "push" ? "pushState" : "replaceState"](
    marker || {},
    "",
    `${window.location.pathname}${suffix ? `?${suffix}` : ""}`,
  );
}

function formatAmount(value: number | null, currency: string | null): string {
  if (value === null || !currency) return "Not available";
  try {
    return new Intl.NumberFormat("en-GB", {
      style: "currency",
      currency,
      notation: Math.abs(value) >= 1_000_000 ? "compact" : "standard",
      maximumFractionDigits: Math.abs(value) >= 1_000_000 ? 1 : 0,
    }).format(value);
  } catch {
    return `${currency} ${value.toLocaleString("en-GB")}`;
  }
}

function profileStatusLabel(profile: ProfileLink): string {
  if (profile.status === "single") return "Linked profile";
  if (profile.status === "multiple") return "Observed only · link unresolved";
  return "Observed only";
}

export default function DonorDirectoryPage({
  apiBase,
  online,
  selectedSources,
  onHeaderStateChange,
  onBackToLandscape,
  onOpenOrganizationResearch,
  onOpenRegistrySearch,
  onOpenProfile,
  favoriteDonorKeys,
  onToggleFavoriteDonor,
  onCloseFavoriteDonor,
}: Props) {
  const initial = useMemo(() => routeState(selectedSources), [selectedSources]);
  const [scope, setScope] = useState<GrantScope>(initial.scope);
  const [draft, setDraft] = useState<GrantScope>(initial.scope);
  const [directory, setDirectory] = useState<DonorDirectoryState>(initial.directory);
  const [searchDraft, setSearchDraft] = useState(initial.directory.search);
  const [result, setResult] = useState<DonorListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [selectedKey, setSelectedKey] = useState<string | undefined>(initial.directory.donorKey);
  const [detail, setDetail] = useState<DonorDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [activityLoaded, setActivityLoaded] = useState(false);
  const requestVersion = useRef(0);
  const detailVersion = useRef(0);
  const listScrollPosition = useRef(0);
  const drawerRef = useRef<HTMLElement>(null);
  const detailRef = useRef<HTMLElement>(null);
  const selectedRowRef = useRef<HTMLButtonElement>(null);

  const filterCount = grantScopeFilterCount(scope)
    + Number(Boolean(directory.search))
    + Number(directory.status !== "all")
    + Number(directory.sort !== "largest_observed_funding");
  const resetDisabled = filterCount === 0;

  useEffect(() => {
    setScope(current => {
      return normalizeGrantScope({ ...current, sources: selectedSources });
    });
    setDraft(current => normalizeGrantScope({ ...current, sources: selectedSources }));
  }, [selectedSources]);

  useEffect(() => {
    onHeaderStateChange({ filterCount, resetDisabled, filtersExpanded: filtersOpen });
  }, [filterCount, filtersOpen, onHeaderStateChange, resetDisabled]);

  useEffect(() => {
    if (!filtersOpen) return;
    drawerRef.current?.focus();
    const handleDrawerKeys = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setFiltersOpen(false);
        window.setTimeout(() => document.querySelector<HTMLButtonElement>(".app-header-filter")?.focus(), 0);
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
    window.addEventListener("keydown", handleDrawerKeys);
    return () => window.removeEventListener("keydown", handleDrawerKeys);
  }, [filtersOpen]);

  useEffect(() => () => {
    onHeaderStateChange({ filterCount: 0, resetDisabled: true, filtersExpanded: false });
  }, [onHeaderStateChange]);

  useEffect(() => {
    const open = () => {
      setDraft(scope);
      setFiltersOpen(true);
    };
    const reset = () => {
      const nextScope = emptyGrantScope(scope.sources);
      const nextDirectory = { ...DEFAULT_DONOR_DIRECTORY_STATE };
      setScope(nextScope);
      setDraft(nextScope);
      setDirectory(nextDirectory);
      setSearchDraft("");
      setSelectedKey(undefined);
      setDetail(null);
      setActivityLoaded(false);
      setFiltersOpen(false);
      writeRoute(nextScope, nextDirectory);
    };
    window.addEventListener("donor-directory-open-filters", open);
    window.addEventListener("donor-directory-reset", reset);
    return () => {
      window.removeEventListener("donor-directory-open-filters", open);
      window.removeEventListener("donor-directory-reset", reset);
    };
  }, [scope]);

  useEffect(() => {
    const pop = () => {
      const next = routeState(selectedSources);
      setScope(next.scope);
      setDraft(next.scope);
      setDirectory(next.directory);
      setSearchDraft(next.directory.search);
      setSelectedKey(next.directory.donorKey);
      if (!next.directory.donorKey) {
        setDetail(null);
        setActivityLoaded(false);
        window.requestAnimationFrame(() => {
          window.scrollTo({ top: listScrollPosition.current });
          selectedRowRef.current?.focus();
        });
      }
    };
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, [selectedSources]);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      if (searchDraft === directory.search) return;
      const next = { ...directory, search: searchDraft.trim(), page: 1, donorKey: undefined };
      setDirectory(next);
      setSelectedKey(undefined);
      setDetail(null);
      writeRoute(scope, next);
    }, 250);
    return () => window.clearTimeout(timeout);
  }, [directory, scope, searchDraft]);

  useEffect(() => {
    if (!online || !scope.beneficiaryCountry) {
      setLoading(false);
      setResult(null);
      return;
    }
    const controller = new AbortController();
    const version = ++requestVersion.current;
    const params = grantScopeToApiParams(scope, { requireCountry: true });
    params.set("search", directory.search);
    params.set("profile_status", directory.status);
    params.set("sort", directory.sort);
    params.set("page", String(directory.page));
    params.set("page_size", "25");
    setLoading(true);
    setError(null);
    fetch(`${apiBase}/api/charities/grants/funders?${params.toString()}`, {
      credentials: "include",
      signal: controller.signal,
    })
      .then(async response => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || `Donor Directory request failed (${response.status}).`);
        return body as DonorListResponse;
      })
      .then(body => {
        if (version === requestVersion.current) setResult(body);
      })
      .catch(reason => {
        if ((reason as Error).name !== "AbortError" && version === requestVersion.current) {
          setError((reason as Error).message || "Donor Directory is temporarily unavailable.");
        }
      })
      .finally(() => {
        if (version === requestVersion.current) setLoading(false);
      });
    return () => controller.abort();
  }, [apiBase, directory.page, directory.search, directory.sort, directory.status, online, scope]);

  const fetchDetail = useCallback((key: string, full: boolean) => {
    if (!online || !scope.beneficiaryCountry) return;
    const controller = new AbortController();
    const version = ++detailVersion.current;
    const params = grantScopeToApiParams(scope, { requireCountry: true });
    params.set("detail_level", full ? "full" : "summary");
    setDetailLoading(true);
    setDetailError(null);
    fetch(`${apiBase}/api/charities/grants/funders/${encodeURIComponent(key)}?${params.toString()}`, {
      credentials: "include",
      signal: controller.signal,
    })
      .then(async response => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || `Donor detail request failed (${response.status}).`);
        return body as DonorDetail;
      })
      .then(body => {
        if (version !== detailVersion.current) return;
        setDetail(body);
        setActivityLoaded(full);
      })
      .catch(reason => {
        if ((reason as Error).name !== "AbortError" && version === detailVersion.current) {
          setDetailError((reason as Error).message || "Donor detail is temporarily unavailable.");
        }
      })
      .finally(() => {
        if (version === detailVersion.current) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [apiBase, online, scope]);

  useEffect(() => {
    if (!selectedKey) return;
    return fetchDetail(selectedKey, false);
  }, [fetchDetail, selectedKey]);

  useEffect(() => {
    if (!selectedKey) return;
    detailRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (onCloseFavoriteDonor) {
        onCloseFavoriteDonor();
        return;
      }
      if (window.history.state?.donorDetail) {
        window.history.back();
        return;
      }
      const next = { ...directory, donorKey: undefined };
      setDirectory(next);
      setSelectedKey(undefined);
      setDetail(null);
      setActivityLoaded(false);
      writeRoute(scope, next, "push");
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [directory, onCloseFavoriteDonor, scope, selectedKey]);

  useEffect(() => {
    if (!selectedKey || !detail) return;
    const frame = window.requestAnimationFrame(() => {
      const detailElement = detailRef.current;
      if (!detailElement) return;
      const contentElement = detailElement.querySelector<HTMLElement>(".donor-detail-content") || detailElement;
      const headerHeight = document.querySelector<HTMLElement>(".header-bar")?.getBoundingClientRect().height || 0;
      const destination = window.scrollY + contentElement.getBoundingClientRect().top - headerHeight - 16;
      window.scrollTo({ top: Math.max(0, destination), behavior: "smooth" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [detail, selectedKey]);

  const applyFilters = () => {
    if (draft.dateFrom && draft.dateTo && draft.dateFrom > draft.dateTo) {
      setError("The period start cannot be after the period end.");
      return;
    }
    const next = normalizeGrantScope({ ...draft, sources: scope.sources });
    const nextDirectory = { ...directory, page: 1, donorKey: undefined };
    setScope(next);
    setDirectory(nextDirectory);
    setSelectedKey(undefined);
    setDetail(null);
    setFiltersOpen(false);
    writeRoute(next, nextDirectory);
  };

  const openDetail = (item: DonorResult) => {
    listScrollPosition.current = window.scrollY;
    const next = { ...directory, donorKey: item.source_funder_key };
    setDirectory(next);
    setSelectedKey(item.source_funder_key);
    setDetail(null);
    setActivityLoaded(false);
    writeRoute(scope, next, "push", { donorDetail: true });
  };

  const closeDetail = () => {
    if (onCloseFavoriteDonor) {
      onCloseFavoriteDonor();
      return;
    }
    if (window.history.state?.donorDetail) {
      window.history.back();
      return;
    }
    const next = { ...directory, donorKey: undefined };
    setDirectory(next);
    setSelectedKey(undefined);
    setDetail(null);
    setActivityLoaded(false);
    writeRoute(scope, next, "push");
  };

  const setStatus = (status: DonorDirectoryState["status"]) => {
    const next = { ...directory, status, page: 1, donorKey: undefined };
    setDirectory(next);
    setSelectedKey(undefined);
    writeRoute(scope, next);
  };

  const setSort = (sort: DonorDirectoryState["sort"]) => {
    const next = { ...directory, sort, page: 1, donorKey: undefined };
    setDirectory(next);
    setSelectedKey(undefined);
    writeRoute(scope, next);
  };

  const setPage = (page: number) => {
    const next = { ...directory, page, donorKey: undefined };
    setDirectory(next);
    setSelectedKey(undefined);
    writeRoute(scope, next, "push");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const removeChip = (key: keyof GrantScope, label: string) => {
    const value = key === "programmeAreas" || key === "beneficiaryGeographies" ? label : undefined;
    const next = removeGrantScopeValue(scope, key, value);
    const nextDirectory = { ...directory, page: 1, donorKey: undefined };
    setScope(next);
    setDraft(next);
    setDirectory(nextDirectory);
    setSelectedKey(undefined);
    writeRoute(next, nextDirectory);
  };

  const chips = grantScopeChips(scope);
  const countryLabel = result?.country.name || scope.beneficiaryCountry;
  const statusCounts = result?.summary.status_counts || { all: 0, linked: 0, observed_only: 0 };
  const favoritePayload = (item: DonorResult, country: string | null): FavoriteDonorPayload => ({
    key: item.source_funder_key,
    name: item.display_name,
    route: window.location.search,
    funding: formatAmount(item.observed_activity.observed_funding, item.observed_activity.displayed_currency),
    grantCount: item.observed_activity.grant_count,
    recipientCount: item.observed_activity.recipient_count,
    country,
    savedAt: Date.now(),
  });

  return (
    <section className="donor-directory-page" aria-labelledby="donor-directory-title">
      <div className="page-introduction donor-directory-introduction">
        <div>
          <span className="page-eyebrow">Observed grant relationships</span>
          <h2 id="donor-directory-title">Donor Directory</h2>
          <p>{countryLabel ? `Funders observed in ${countryLabel}.` : "Funders observed in the selected beneficiary geography."}</p>
        </div>
        <button type="button" className="btn btn-secondary" onClick={() => onBackToLandscape(scope)}>
          <ArrowLeft size={16} /> Funding Landscape
        </button>
      </div>

      {chips.length > 0 && (
        <div className="active-filter-chips" aria-label="Active grant scope">
          {chips.map(chip => (
            <button type="button" key={`${chip.key}-${chip.label}`} onClick={() => removeChip(chip.key, chip.label)}>
              {chip.label} <X size={13} aria-hidden="true" />
            </button>
          ))}
        </div>
      )}

      <section className="donor-directory-secondary" aria-label="Other organization research paths">
        <span className="donor-directory-secondary-label">Other research paths</span>
        <div className="donor-directory-secondary-links">
          <button type="button" onClick={onOpenOrganizationResearch}><Building2 size={18} /><span><strong>Organization Research</strong><small>Explore enriched profiles; inclusion does not imply observed funding.</small></span><ArrowRight size={16} /></button>
          <button type="button" onClick={onOpenRegistrySearch}><FileSearch size={18} /><span><strong>Advanced Charity Commission Search</strong><small>Search official registry records and registered locations.</small></span><ArrowRight size={16} /></button>
        </div>
      </section>

      {!scope.beneficiaryCountry ? (
        <div className="directory-empty-state donor-directory-no-country">
          <h3>Select a beneficiary country on the Funding Landscape</h3>
          <p>The Donor Directory is grounded in source funders observed sending grants to that beneficiary geography. It does not use registered office location.</p>
          <button type="button" className="btn btn-primary" onClick={() => onBackToLandscape(scope)}>Open Funding Landscape <ArrowRight size={16} /></button>
        </div>
      ) : (
        <>
          <div className="donor-directory-toolbar">
            <label className="donor-search">
              <span className="sr-only">Search observed funders</span>
              <Search size={17} aria-hidden="true" />
              <input
                value={searchDraft}
                onChange={event => setSearchDraft(event.target.value)}
                placeholder="Search observed funders…"
                maxLength={160}
              />
            </label>
            <label className="donor-sort">
              <span>Sort</span>
              <select value={directory.sort} onChange={event => setSort(event.target.value as DonorDirectoryState["sort"])}>
                <option value="largest_observed_funding">Largest observed funding</option>
                <option value="most_grants">Most grants</option>
                <option value="most_recently_active">Most recently active</option>
              </select>
            </label>
          </div>

          <div className="donor-status-tabs" role="group" aria-label="Donor profile status">
            <button type="button" className={directory.status === "all" ? "active" : ""} aria-pressed={directory.status === "all"} onClick={() => setStatus("all")}>All observed <span>{statusCounts.all}</span></button>
            <button type="button" className={directory.status === "linked" ? "active" : ""} aria-pressed={directory.status === "linked"} onClick={() => setStatus("linked")}>Linked profiles <span>{statusCounts.linked}</span></button>
            <button type="button" className={directory.status === "observed_only" ? "active" : ""} aria-pressed={directory.status === "observed_only"} onClick={() => setStatus("observed_only")}>Observed only <span>{statusCounts.observed_only}</span></button>
          </div>

          {error && <div className="data-notice data-notice-error" role="alert">{error}</div>}
          {!online && <div className="data-notice data-notice-warning">The local backend is required for observed donor results.</div>}
          {loading && <div className="donor-directory-loading" role="status" aria-live="polite"><LoaderCircle className="spin" size={22} /> Loading observed donors…</div>}

          {!loading && result?.items.length === 0 && (
            <div className="directory-empty-state">
              <h3>No observed funders match this scope</h3>
              <p>Try a shorter donor search or fewer grant-scope filters. This result does not imply that no organization exists.</p>
              <button type="button" className="btn btn-secondary" onClick={() => window.dispatchEvent(new Event("donor-directory-reset"))}>Reset directory</button>
            </div>
          )}

          {!loading && result && result.items.length > 0 && (
            <div className={`donor-directory-workspace${selectedKey ? " has-detail" : ""}`}>
              <div className="donor-results-pane">
                <div className="donor-list-summary">
                  <strong>{result.pagination.total_items.toLocaleString("en-GB")} observed funders</strong>
                  <span>{result.summary.matching_grant_count.toLocaleString("en-GB")} matching grants</span>
                </div>
                <ol className="donor-result-list">
                  {result.items.map(item => {
                    const isFavorite = favoriteDonorKeys.includes(item.source_funder_key);
                    return <li className="donor-result-item" key={item.source_funder_key}>
                      <button
                        ref={selectedKey === item.source_funder_key ? selectedRowRef : undefined}
                        type="button"
                        className={`donor-result-open${selectedKey === item.source_funder_key ? " selected" : ""}`}
                        aria-pressed={selectedKey === item.source_funder_key}
                        onClick={() => openDetail(item)}
                      >
                        <span className="donor-row-main">
                          <strong>{item.display_name}</strong>
                          <span className={`donor-status-label ${item.profile_link.status === "single" ? "linked" : "observed"}`}>
                            {profileStatusLabel(item.profile_link)}
                          </span>
                          <small>
                            {item.observed_activity.grant_count.toLocaleString("en-GB")} grants · {item.observed_activity.recipient_count.toLocaleString("en-GB")} recipients
                            {item.observed_activity.programme_areas[0]?.name ? ` · ${item.observed_activity.programme_areas[0].name}` : ""}
                          </small>
                        </span>
                        <span className="donor-row-value">
                          <strong>{formatAmount(item.observed_activity.observed_funding, item.observed_activity.displayed_currency)}</strong>
                          <ChevronRight size={18} aria-hidden="true" />
                        </span>
                      </button>
                      <button
                        type="button"
                        className={`favorite-toggle donor-favorite-toggle${isFavorite ? " is-favorite" : ""}`}
                        aria-label={`${isFavorite ? "Remove" : "Add"} ${item.display_name} ${isFavorite ? "from" : "to"} favorites`}
                        aria-pressed={isFavorite}
                        onClick={() => onToggleFavoriteDonor(favoritePayload(item, result.country.name))}
                      ><Star size={16} fill={isFavorite ? "currentColor" : "none"} /></button>
                    </li>;
                  })}
                </ol>
                {result.pagination.total_pages > 1 && (
                  <nav className="source-funder-pagination" aria-label="Donor result pages">
                    <button type="button" className="btn btn-secondary" disabled={directory.page <= 1} onClick={() => setPage(directory.page - 1)}><ChevronLeft size={16} /> Previous</button>
                    <span>Page {result.pagination.page} of {result.pagination.total_pages}</span>
                    <button type="button" className="btn btn-secondary" disabled={directory.page >= result.pagination.total_pages} onClick={() => setPage(directory.page + 1)}>Next <ChevronRight size={16} /></button>
                  </nav>
                )}
              </div>

              {selectedKey && (
                <aside className="donor-detail-shell" ref={detailRef} tabIndex={-1} aria-label="Donor details">
                  <div className="donor-detail-header">
                    <button type="button" className="donor-detail-close" onClick={closeDetail} aria-label="Close donor details"><X size={19} /></button>
                    {detail ? (
                      <>
                        <button
                          type="button"
                          className={`favorite-toggle donor-detail-favorite${favoriteDonorKeys.includes(detail.funder.source_funder_key) ? " is-favorite" : ""}`}
                          aria-label={`${favoriteDonorKeys.includes(detail.funder.source_funder_key) ? "Remove" : "Add"} ${detail.funder.display_name} ${favoriteDonorKeys.includes(detail.funder.source_funder_key) ? "from" : "to"} favorites`}
                          aria-pressed={favoriteDonorKeys.includes(detail.funder.source_funder_key)}
                          onClick={() => onToggleFavoriteDonor(favoritePayload(detail.funder, detail.country.name))}
                        ><Star size={16} fill={favoriteDonorKeys.includes(detail.funder.source_funder_key) ? "currentColor" : "none"} /></button>
                        <span className={`donor-status-label ${detail.funder.profile_link.status === "single" ? "linked" : "observed"}`}>{profileStatusLabel(detail.funder.profile_link)}</span>
                        <h3>{detail.funder.display_name}</h3>
                        <p>Beneficiary geography: {detail.country.name}</p>
                      </>
                    ) : <h3>Donor details</h3>}
                  </div>
                  {detailLoading && !detail && <div className="donor-detail-loading" role="status"><LoaderCircle className="spin" size={22} /> Loading donor summary…</div>}
                  {detailError && <div className="data-notice data-notice-error" role="alert">{detailError}<button type="button" onClick={() => fetchDetail(selectedKey, activityLoaded)}>Retry</button></div>}
                  {detail && (
                    <div className="donor-detail-content">
                      <section aria-labelledby="donor-observed-heading">
                        <h4 id="donor-observed-heading">Observed activity</h4>
                        <div className="donor-detail-metrics">
                          <div><span>Observed funding</span><strong>{formatAmount(detail.funder.observed_funding.amount, detail.funder.observed_funding.currency)}</strong></div>
                          <div><span>Grants</span><strong>{detail.funder.activity.grant_count.toLocaleString("en-GB")}</strong></div>
                          <div><span>Recipients</span><strong>{detail.funder.activity.distinct_recipient_count.toLocaleString("en-GB")}</strong></div>
                          <div><span>Latest activity</span><strong>{detail.funder.activity.latest_award_date || "Unknown"}</strong></div>
                        </div>
                        <div className="donor-detail-programmes">
                          {detail.funder.observed_activity.programme_areas.map(area => <span key={area.name}>{area.name}</span>)}
                        </div>
                      </section>

                      <details className="donor-detail-section" onToggle={event => {
                        const section = event.currentTarget as HTMLDetailsElement;
                        if (!section.open) return;
                        window.requestAnimationFrame(() => section.scrollIntoView({ behavior: "smooth", block: "start" }));
                        if (!activityLoaded && !detailLoading) fetchDetail(selectedKey, true);
                      }}>
                        <summary>Grant activity and recipients <ChevronRight size={16} /></summary>
                        {detailLoading && <div className="donor-section-loading" role="status"><LoaderCircle className="spin" size={18} /> Loading grant relationships…</div>}
                        {activityLoaded && (
                          <div className="donor-relationship-list">
                            <h5>Top recipients</h5>
                            {detail.top_recipients.length ? detail.top_recipients.slice(0, 20).map(recipient => (
                              <div className="donor-recipient-row" key={recipient.recipient_key}>
                                <span><strong>{recipient.name}</strong><small>{recipient.grant_count} grants · latest {recipient.latest_award_date || "unknown"}</small></span>
                                <strong>{formatAmount(recipient.observed_funding, recipient.currency)}</strong>
                              </div>
                            )) : <p>No country-attributable recipient funding is available.</p>}
                            <h5>Latest observed grants</h5>
                            {detail.grant_sample.slice(0, 12).map(grant => (
                              <article className="donor-grant-row" key={grant.grant_id}>
                                <div><strong>{grant.recipient_name}</strong><small>{grant.award_date || "Date unavailable"} · {grant.grant_id}</small></div>
                                <span>{formatAmount(grant.amount, grant.currency)}</span>
                              </article>
                            ))}
                          </div>
                        )}
                      </details>

                      <section className="donor-detail-section linked-profile-section">
                        <h4>Linked organization profile</h4>
                        {detail.funder.profile_link.status === "single" ? (
                          <div>
                            <strong>{detail.funder.profile_link.profile_name}</strong>
                            <p>Profile information is linked explicitly and remains separate from observed grant facts.</p>
                            <button type="button" className="btn btn-secondary" onClick={() => {
                              const link = detail.funder.profile_link;
                              if (link.status === "single") onOpenProfile(link.profile_id, link.profile_name);
                            }}><Building2 size={15} /> Open organization research</button>
                          </div>
                        ) : (
                          <p>This funder appears in observed grant data. No linked organization profile is currently available.</p>
                        )}
                      </section>

                      <details className="donor-detail-section source-evidence-section" onToggle={event => {
                        const section = event.currentTarget as HTMLDetailsElement;
                        if (!section.open) return;
                        window.requestAnimationFrame(() => section.scrollIntoView({ behavior: "smooth", block: "start" }));
                        if (!activityLoaded && !detailLoading) fetchDetail(selectedKey, true);
                      }}>
                        <summary>Source evidence and methodology <ChevronRight size={16} /></summary>
                        {detailLoading && <div className="donor-section-loading"><LoaderCircle className="spin" size={18} /> Loading source evidence…</div>}
                        {activityLoaded && (
                          <div className="evidence-link-list">
                            <div className="source-record-identifier">
                              <span><strong>Source organization identifier</strong><code>{detail.funder.identity.source_organization_id || detail.funder.identity.normalized_name_fallback || "Not supplied"}</code></span>
                              {(detail.funder.identity.source_organization_id || detail.funder.identity.normalized_name_fallback) && <button type="button" onClick={() => navigator.clipboard?.writeText(detail.funder.identity.source_organization_id || detail.funder.identity.normalized_name_fallback || "")} aria-label="Copy source organization identifier"><Copy size={14} /></button>}
                            </div>
                            {detail.source_evidence.length ? detail.source_evidence.map((evidence, index) => (
                              <a key={`${evidence.kind}-${evidence.url}-${index}`} href={evidence.url} target="_blank" rel="noopener noreferrer">
                                <span><strong>{evidence.label}</strong><small>{evidence.origin.replaceAll("_", " ")}{evidence.role ? ` · ${evidence.role}` : ""}</small></span>
                                <ExternalLink size={15} aria-hidden="true" />
                              </a>
                            )) : <p>No safe HTTP(S) evidence link is stored for this sample.</p>}
                            <p className="evidence-policy">Links come from stored source records. The platform does not fetch, preflight, proxy, or verify external destinations.</p>
                          </div>
                        )}
                      </details>
                    </div>
                  )}
                </aside>
              )}
            </div>
          )}
        </>
      )}

      {filtersOpen && (
        <div className="filter-drawer-backdrop" onMouseDown={event => {
          if (event.currentTarget === event.target) setFiltersOpen(false);
        }}>
          <aside className="filter-drawer donor-filter-drawer" ref={drawerRef} tabIndex={-1} aria-modal="true" role="dialog" aria-labelledby="donor-filter-title">
            <div className="filter-drawer-header"><div><span>Donor Directory</span><h3 id="donor-filter-title">Filters</h3></div><button type="button" onClick={() => setFiltersOpen(false)} aria-label="Close filters"><X size={20} /></button></div>
            <div className="filter-drawer-body">
              <label><span>Beneficiary country (ISO)</span><input value={draft.beneficiaryCountry || ""} maxLength={2} onChange={event => setDraft(current => ({ ...current, beneficiaryCountry: event.target.value }))} placeholder="NG" /></label>
              <div className="filter-date-grid"><label><span>From</span><input type="date" value={draft.dateFrom || ""} onChange={event => setDraft(current => ({ ...current, dateFrom: event.target.value }))} /></label><label><span>To</span><input type="date" value={draft.dateTo || ""} onChange={event => setDraft(current => ({ ...current, dateTo: event.target.value }))} /></label></div>
              <label><span>Currency</span><select value={draft.currency || "AUTO"} onChange={event => setDraft(current => ({ ...current, currency: event.target.value }))}><option value="AUTO">Auto · converted EUR</option>{(result?.available_currencies || []).map(currency => <option key={currency} value={currency}>{currency} only</option>)}</select></label>
              <label><span>Programme areas</span><input value={draft.programmeAreas.join(", ")} onChange={event => setDraft(current => ({ ...current, programmeAreas: event.target.value.split(",").map(value => value.trim()).filter(Boolean) }))} placeholder="tech-enablement" /></label>
              <label><span>Beneficiary geography terms</span><input value={draft.beneficiaryGeographies.join(", ")} onChange={event => setDraft(current => ({ ...current, beneficiaryGeographies: event.target.value.split(",").map(value => value.trim()).filter(Boolean) }))} /></label>
              <label><span>Donor contains</span><input value={draft.donor || ""} onChange={event => setDraft(current => ({ ...current, donor: event.target.value }))} /></label>
              <label><span>Recipient contains</span><input value={draft.recipient || ""} onChange={event => setDraft(current => ({ ...current, recipient: event.target.value }))} /></label>
              <div className="synchronized-source-note"><SlidersHorizontal size={16} /><span>Data sources are controlled by the synchronized header action: {scope.sources.length ? scope.sources.join(", ") : "none selected"}.</span></div>
            </div>
            <div className="filter-drawer-footer"><button type="button" className="btn btn-secondary" onClick={() => setDraft(scope)}>Reset edits</button><button type="button" className="btn btn-primary" onClick={applyFilters}>Apply filters</button></div>
          </aside>
        </div>
      )}
    </section>
  );
}
