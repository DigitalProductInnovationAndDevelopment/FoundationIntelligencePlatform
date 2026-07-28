import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, Building2, LoaderCircle, Search, X } from "lucide-react";
import { validateOptionalNumericRange } from "../lib/numericRange";

interface RegistrySummary {
  registry_id: string;
  charity_number: string;
  registered_name: string;
  registration_status: string | null;
  income: number | null;
  expenditure: number | null;
  city: string | null;
  administrative_region: string | null;
  country_code: string | null;
  source_record_updated_at: string | null;
  has_enriched_profile: boolean;
  has_grant_data: boolean;
  has_philea_data: boolean;
}

interface RegistryPage {
  results: RegistrySummary[];
  next_cursor: string | null;
  has_more: boolean;
  page_size: number;
  search_strategy: string;
}

interface RegistryDetail {
  registry_id: string;
  charity_number: string;
  linked_charity_number: string | null;
  registered_name: string;
  registration_status: string | null;
  registration_date: string | null;
  removal_date: string | null;
  income: number | null;
  expenditure: number | null;
  financial_period_end_date: string | null;
  address_lines: string[];
  postcode: string | null;
  city: string | null;
  administrative_region: string | null;
  country_code: string | null;
  activity_text: string | null;
  source_name: string;
  source_record_updated_at: string | null;
  imported_at: string;
  is_current_source_record: boolean;
  observed_grant_data_message: string;
  enriched_profile: null | {
    enriched_organization_id: number;
    organization_name: string;
    match_status: string;
    match_method: string;
    match_confidence: number | null;
    match_reason: string | null;
    has_grant_data: boolean;
    has_philea_data: boolean;
  };
}

interface RegistryDirectoryProps {
  apiBase: string;
  online: boolean;
  initialQuery?: string;
  initialBeneficiaryGeography?: string;
  onOpenEnrichedProfile: (id: number, name: string) => void;
}

const formatCurrency = (value: number | null) => {
  if (value === null || value === undefined) return "Not reported";
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: "GBP",
    maximumFractionDigits: 0,
  }).format(value);
};

const queryBoolean = (value: boolean | null) => value === null ? "" : String(value);

type RegistryEnrichmentRun = {
  status: "starting" | "running" | "success" | "failed";
  progress: number;
  startedAt?: number;
  message: string;
  error?: string;
};

const LOCAL_PROFILE_LINK_DURATION_MS = 7_000;

export default function RegistryDirectory({ apiBase, online, initialQuery = "", initialBeneficiaryGeography = "", onOpenEnrichedProfile }: RegistryDirectoryProps) {
  const [query, setQuery] = useState(initialQuery);
  const [charityNumber, setCharityNumber] = useState("");
  const [status, setStatus] = useState("");
  const [incomeMin, setIncomeMin] = useState("");
  const [incomeMax, setIncomeMax] = useState("");
  const [expenditureMin, setExpenditureMin] = useState("");
  const [expenditureMax, setExpenditureMax] = useState("");
  const [country, setCountry] = useState("");
  const [region, setRegion] = useState("");
  const [beneficiaryGeography, setBeneficiaryGeography] = useState(initialBeneficiaryGeography);
  const [hasEnrichedProfile, setHasEnrichedProfile] = useState<boolean | null>(null);
  const [hasGrantData, setHasGrantData] = useState<boolean | null>(null);
  const [sort, setSort] = useState("name");
  const [page, setPage] = useState<RegistryPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedRegistryId, setSelectedRegistryId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RegistryDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [registryEnrichment, setRegistryEnrichment] = useState<RegistryEnrichmentRun | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const detailRequestRef = useRef<AbortController | null>(null);
  const enrichmentRequestRef = useRef<AbortController | null>(null);
  const requestVersion = useRef(0);
  const enrichmentVersion = useRef(0);
  const selectedRegistryIdRef = useRef(selectedRegistryId);
  const [detailRefreshRevision, setDetailRefreshRevision] = useState(0);
  selectedRegistryIdRef.current = selectedRegistryId;
  const incomeRange = validateOptionalNumericRange(incomeMin, incomeMax, "Income");
  const expenditureRange = validateOptionalNumericRange(expenditureMin, expenditureMax, "Expenditure");
  const rangeValidationError = incomeRange.error || expenditureRange.error;
  const activeFilterCount = Number(Boolean(query.trim()))
    + Number(Boolean(charityNumber.trim()))
    + Number(Boolean(status))
    + Number(Boolean(incomeMin || incomeMax))
    + Number(Boolean(expenditureMin || expenditureMax))
    + Number(Boolean(country))
    + Number(Boolean(region.trim()))
    + Number(Boolean(beneficiaryGeography.trim()))
    + Number(hasEnrichedProfile !== null)
    + Number(hasGrantData !== null)
    + Number(sort !== "name");

  const buildSearchParams = useCallback((cursor?: string | null) => {
    const params = new URLSearchParams({ limit: "50", sort });
    if (query.trim()) params.set("query", query.trim());
    if (charityNumber.trim()) params.set("charity_number", charityNumber.trim());
    if (status) params.set("status", status);
    if (incomeMin) params.set("income_min", incomeMin);
    if (incomeMax) params.set("income_max", incomeMax);
    if (expenditureMin) params.set("expenditure_min", expenditureMin);
    if (expenditureMax) params.set("expenditure_max", expenditureMax);
    if (country) params.set("country", country);
    if (region.trim()) params.set("region", region.trim());
    if (beneficiaryGeography.trim()) params.set("beneficiary_geography", beneficiaryGeography.trim());
    if (hasEnrichedProfile !== null) params.set("has_enriched_profile", queryBoolean(hasEnrichedProfile));
    if (hasGrantData !== null) params.set("has_grant_data", queryBoolean(hasGrantData));
    if (cursor) params.set("cursor", cursor);
    return params;
  }, [beneficiaryGeography, charityNumber, country, expenditureMax, expenditureMin, hasEnrichedProfile, hasGrantData, incomeMax, incomeMin, query, region, sort, status]);

  const loadPage = useCallback(async (cursor?: string | null, append = false) => {
    if (rangeValidationError) {
      requestRef.current?.abort();
      requestVersion.current += 1;
      setLoading(false);
      setLoadingMore(false);
      return;
    }
    if (!online) {
      setPage({ results: [], next_cursor: null, has_more: false, page_size: 50, search_strategy: "offline" });
      setError("The scalable registry directory requires the local BFF.");
      return;
    }
    if (!append) {
      requestRef.current?.abort();
      requestRef.current = new AbortController();
      setLoading(true);
    } else {
      setLoadingMore(true);
    }
    const currentVersion = ++requestVersion.current;
    const controller = append ? new AbortController() : requestRef.current;
    try {
      const response = await fetch(
        `${apiBase}/api/charities/directory/organizations?${buildSearchParams(cursor).toString()}`,
        { credentials: "include", signal: controller?.signal },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `Directory request failed (${response.status}).`);
      if (currentVersion !== requestVersion.current) return;
      setPage(previous => append && previous
        ? { ...payload, results: [...previous.results, ...payload.results] }
        : payload);
      setError(null);
    } catch (requestError) {
      if ((requestError as Error).name !== "AbortError" && currentVersion === requestVersion.current) {
        setError((requestError as Error).message || "The registry directory is temporarily unavailable.");
      }
    } finally {
      if (currentVersion === requestVersion.current) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [apiBase, buildSearchParams, online, rangeValidationError]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadPage();
    }, 300);
    return () => {
      window.clearTimeout(timer);
      requestRef.current?.abort();
    };
  }, [loadPage]);

  const loadDetail = useCallback(async (registryId: string, signal?: AbortSignal) => {
    setDetailLoading(true);
    try {
      const response = await fetch(
        `${apiBase}/api/charities/directory/organizations/${encodeURIComponent(registryId)}`,
        { credentials: "include", signal },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Organization details are temporarily unavailable.");
      if (!signal?.aborted) setDetail(payload);
    } catch (requestError) {
      if (!signal?.aborted && (requestError as Error).name !== "AbortError") {
        setError((requestError as Error).message || "Organization details are temporarily unavailable.");
      }
    } finally {
      if (!signal?.aborted) setDetailLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    enrichmentVersion.current += 1;
    enrichmentRequestRef.current?.abort();
    enrichmentRequestRef.current = null;
  }, [selectedRegistryId]);

  useEffect(() => () => {
    enrichmentRequestRef.current?.abort();
  }, []);

  useEffect(() => {
    if (!selectedRegistryId) {
      detailRequestRef.current?.abort();
      setDetail(null);
      setRegistryEnrichment(null);
      return;
    }
    detailRequestRef.current?.abort();
    const controller = new AbortController();
    detailRequestRef.current = controller;
    setDetail(null);
    setRegistryEnrichment(null);
    void loadDetail(selectedRegistryId, controller.signal);
    return () => controller.abort();
  }, [detailRefreshRevision, selectedRegistryId, loadDetail]);

  const startRegistryEnrichment = useCallback(async () => {
    if (!detail || detail.enriched_profile || registryEnrichment?.status === "running" || registryEnrichment?.status === "starting") return;
    const targetRegistryId = selectedRegistryId;
    if (!targetRegistryId || detail.registry_id !== targetRegistryId) return;
    const charityNumber = [detail.linked_charity_number, detail.charity_number]
      .map(value => Number(value))
      .find(value => Number.isSafeInteger(value) && value > 0);
    if (!charityNumber) {
      setRegistryEnrichment({
        status: "failed",
        progress: 0,
        message: "A usable Charity Commission number is required for this check.",
      });
      return;
    }
    if (!online) {
      setRegistryEnrichment({
        status: "failed",
        progress: 0,
        message: "Reconnect the local backend before starting the profile and grant check.",
      });
      return;
    }

    const startedAt = Date.now();
    const currentVersion = ++enrichmentVersion.current;
    enrichmentRequestRef.current?.abort();
    const controller = new AbortController();
    enrichmentRequestRef.current = controller;
    setRegistryEnrichment({
      status: "starting",
      progress: 5,
      startedAt,
      message: "Linking the cached official record and observed grants",
    });
    try {
      const [response] = await Promise.all([
        fetch(`${apiBase}/api/charities/directory/organizations/enrich`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reg_numbers: [charityNumber] }),
          signal: controller.signal,
        }),
        new Promise(resolve => window.setTimeout(resolve, LOCAL_PROFILE_LINK_DURATION_MS)),
      ]);
      const payload = await response.json();
      if (currentVersion !== enrichmentVersion.current || selectedRegistryIdRef.current !== targetRegistryId) return;
      if (!response.ok) throw new Error(payload.detail || "Could not start the profile and grant check.");
      // The selected official record and already-stored observed grants are
      // linked locally, so this should complete without waiting on a global
      // source pipeline or its progress log.
      if (payload.status === "success") {
        setRegistryEnrichment({
          status: "success",
          progress: 100,
          message: "Profile linked. Opening the available organization profile.",
        });
        setDetailRefreshRevision(current => current + 1);
        void loadPage();
        return;
      }
      throw new Error("The local profile link did not complete.");
    } catch (requestError) {
      if (
        (requestError as Error).name === "AbortError"
        || currentVersion !== enrichmentVersion.current
        || selectedRegistryIdRef.current !== targetRegistryId
      ) return;
      setRegistryEnrichment({
        status: "failed",
        progress: 0,
        message: "Could not start the profile and grant check.",
        error: (requestError as Error).message,
      });
    } finally {
      if (enrichmentRequestRef.current === controller) enrichmentRequestRef.current = null;
    }
  }, [apiBase, detail, loadPage, online, registryEnrichment?.status, selectedRegistryId]);

  useEffect(() => {
    if (registryEnrichment?.status !== "starting") return;
    const startedAt = registryEnrichment.startedAt || Date.now();
    const updateProgress = () => {
      const elapsed = Math.max(0, Date.now() - startedAt);
      const progress = Math.min(94, Math.round(5 + (elapsed / LOCAL_PROFILE_LINK_DURATION_MS) * 89));
      const message = progress < 35
        ? "Confirming the official Charity Commission record"
        : progress < 72
          ? "Creating the Organization Research profile"
          : "Linking already observed grants";
      setRegistryEnrichment(current => current?.status === "starting" && current.startedAt === startedAt
        ? { ...current, progress, message }
        : current);
    };
    updateProgress();
    const timer = window.setInterval(updateProgress, 180);
    return () => window.clearInterval(timer);
  }, [registryEnrichment?.startedAt, registryEnrichment?.status]);

  const resetFilters = () => {
    setQuery("");
    setCharityNumber("");
    setStatus("");
    setIncomeMin("");
    setIncomeMax("");
    setExpenditureMin("");
    setExpenditureMax("");
    setCountry("");
    setRegion("");
    setBeneficiaryGeography("");
    setHasEnrichedProfile(null);
    setHasGrantData(null);
    setSort("name");
    setSelectedRegistryId(null);
  };

  useEffect(() => {
    window.dispatchEvent(new CustomEvent("registry-header-state", {
      detail: {
        filterCount: activeFilterCount,
        resetDisabled: activeFilterCount === 0,
        filtersExpanded: filtersOpen,
      },
    }));
  }, [activeFilterCount, filtersOpen]);

  useEffect(() => {
    const openFilters = () => {
      setFiltersOpen(true);
    };
    window.addEventListener("registry-open-filters", openFilters);
    window.addEventListener("registry-reset-filters", resetFilters);
    return () => {
      window.removeEventListener("registry-open-filters", openFilters);
      window.removeEventListener("registry-reset-filters", resetFilters);
      window.dispatchEvent(new CustomEvent("registry-header-state", {
        detail: { filterCount: 0, resetDisabled: true, filtersExpanded: false },
      }));
    };
  }, []);

  return (
    <section className="registry-directory">
      {filtersOpen && <div className="filter-drawer-backdrop" onMouseDown={() => setFiltersOpen(false)}>
        <aside className="filter-drawer registry-filters registry-filter-drawer" role="dialog" aria-modal="true" aria-label="Advanced Charity Commission Search filters" onMouseDown={event => event.stopPropagation()}>
          <div className="filter-drawer-header">
            <div><span>Advanced Charity Search</span><h3>Filters</h3></div>
            <button type="button" onClick={() => setFiltersOpen(false)} aria-label="Close filters"><X size={18} /></button>
          </div>
          <div id="advanced-registry-filters" className="filter-drawer-body">
        <p className="registry-filter-intro">Official Charity Commission records. Registry presence does not imply funding activity.</p>
        <label>
          <span>Charity number</span>
          <input className="form-input" value={charityNumber} onChange={event => setCharityNumber(event.target.value)} placeholder="Exact registered number" inputMode="numeric" />
        </label>
        <label>
          <span>Registration status</span>
          <select className="form-input" value={status} onChange={event => setStatus(event.target.value)}>
            <option value="">Any status</option>
            <option value="Registered">Registered</option>
            <option value="Removed">Removed</option>
          </select>
        </label>
        <div className="registry-filter-pair">
          <label><span>Income from</span><input className="form-input" type="number" min="0" value={incomeMin} onChange={event => setIncomeMin(event.target.value)} placeholder="£" /></label>
          <label><span>Income to</span><input className="form-input" type="number" min="0" value={incomeMax} onChange={event => setIncomeMax(event.target.value)} placeholder="£" /></label>
        </div>
        {incomeRange.error && <small className="registry-enrichment-error" role="alert">{incomeRange.error}</small>}
        <div className="registry-filter-pair">
          <label><span>Expenditure from</span><input className="form-input" type="number" min="0" value={expenditureMin} onChange={event => setExpenditureMin(event.target.value)} placeholder="£" /></label>
          <label><span>Expenditure to</span><input className="form-input" type="number" min="0" value={expenditureMax} onChange={event => setExpenditureMax(event.target.value)} placeholder="£" /></label>
        </div>
        {expenditureRange.error && <small className="registry-enrichment-error" role="alert">{expenditureRange.error}</small>}
        <div className="registry-filter-pair">
          <label><span>Registered country</span><select className="form-input" value={country} onChange={event => setCountry(event.target.value)}><option value="">All</option><option value="GB">United Kingdom</option></select></label>
          <label><span>Registered region</span><input className="form-input" value={region} onChange={event => setRegion(event.target.value)} placeholder="e.g. Somerset" /></label>
        </div>
        <label>
          <span>Observed beneficiary geography</span>
          <input className="form-input" value={beneficiaryGeography} onChange={event => setBeneficiaryGeography(event.target.value)} placeholder="Grant-country filter only" />
        </label>
        <label>
          <span>Profile layer</span>
          <select className="form-input" value={queryBoolean(hasEnrichedProfile)} onChange={event => setHasEnrichedProfile(event.target.value === "" ? null : event.target.value === "true")}>
            <option value="">All registry entries</option><option value="true">Enriched profiles only</option><option value="false">Registry-only entries</option>
          </select>
        </label>
        <label>
          <span>Observed grants</span>
          <select className="form-input" value={queryBoolean(hasGrantData)} onChange={event => setHasGrantData(event.target.value === "" ? null : event.target.value === "true")}>
            <option value="">Any coverage</option><option value="true">Observed grant data only</option><option value="false">No observed grant data</option>
          </select>
        </label>
        <label>
          <span>Sort</span>
          <select className="form-input" value={sort} onChange={event => setSort(event.target.value)}>
            <option value="name">Name</option><option value="income_desc">Income, high to low</option><option value="expenditure_desc">Expenditure, high to low</option>
          </select>
        </label>
        <button type="button" className="btn btn-secondary" onClick={resetFilters}>Reset filters</button>
          </div>
        </aside>
      </div>}

      <div className="registry-primary-search">
        <div>
          <span className="registry-primary-search-label">Find a charity</span>
          <p>Search the official Charity Commission registry by organization name.</p>
        </div>
        <div className="registry-search-input registry-primary-search-input">
          <Search size={17} />
          <input
            value={query}
            onChange={event => setQuery(event.target.value)}
            placeholder="Search organization name…"
            aria-label="Search Charity Commission organizations by name"
          />
          {query && <button type="button" className="registry-search-clear" onClick={() => setQuery("")} aria-label="Clear organization name search">Clear</button>}
        </div>
      </div>

      <div className="registry-results">
        <div className="registry-results-header">
          <div><h3>Registry results</h3><p>Search is server-side; 50 lightweight registry results are requested at a time.</p></div>
          {page && <span className="status-badge">{page.search_strategy === "fts5" ? "Indexed name search" : "Indexed directory"}</span>}
        </div>
        {rangeValidationError && <div className="data-notice data-notice-warning" role="alert">{rangeValidationError} Results are not refreshed until the range is valid.</div>}
        {error && <div className="data-notice data-notice-warning">{error}</div>}
        {loading && !page ? <div className="registry-loading"><LoaderCircle size={22} /> Loading organization directory…</div> : (
          <>
            <div className="registry-result-grid">
              {(page?.results || []).map(organization => (
                <button type="button" className="glass-card registry-result-card" key={organization.registry_id} onClick={() => setSelectedRegistryId(organization.registry_id)}>
                  <div className="registry-result-heading"><span className="charity-card-id">Charity #{organization.charity_number}</span><ArrowRight size={16} /></div>
                  <h3>{organization.registered_name}</h3>
                  <div className="registry-badges">
                    {organization.registration_status && <span className="status-badge">{organization.registration_status}</span>}
                    {organization.has_enriched_profile && <span className="status-badge">Enriched profile</span>}
                    {organization.has_grant_data && <span className="status-badge">Observed grant data</span>}
                    {organization.has_philea_data && <span className="status-badge">Additional Philea data</span>}
                  </div>
                  <div className="registry-result-meta"><span>{organization.city || organization.administrative_region || "Location not reported"}</span><span>{formatCurrency(organization.income)}</span></div>
                </button>
              ))}
            </div>
            {!loading && page?.results.length === 0 && <div className="glass-card directory-empty-state"><div className="directory-empty-icon"><Building2 size={22} /></div><h3>No registry organizations found</h3><p>Try a shorter name, an exact charity number, or fewer filters. No records are downloaded to the browser beyond this result page.</p><button type="button" className="btn btn-secondary" onClick={resetFilters}>Reset directory filters</button></div>}
            {page?.has_more && <button type="button" className="btn btn-secondary registry-load-more" disabled={loadingMore} onClick={() => void loadPage(page.next_cursor, true)}>{loadingMore ? "Loading…" : "Load 50 more organizations"}</button>}
          </>
        )}
      </div>

      {selectedRegistryId && (
        <div className="registry-detail-backdrop" role="presentation" onMouseDown={() => setSelectedRegistryId(null)}>
          <section className="glass-card registry-detail-modal" role="dialog" aria-modal="true" aria-label="Registry organization detail" onMouseDown={event => event.stopPropagation()}>
            <button type="button" className="registry-detail-close" onClick={() => setSelectedRegistryId(null)} aria-label="Close organization detail"><X size={18} /></button>
            {detailLoading || !detail ? <div className="registry-loading"><LoaderCircle size={22} /> Loading registry detail…</div> : <>
              <span className="charity-card-id">Charity Commission · #{detail.charity_number}</span>
              <h2>{detail.registered_name}</h2>
              <div className="registry-badges"><span className="status-badge">{detail.registration_status || "Status not reported"}</span>{detail.enriched_profile && <span className="status-badge">Accepted enriched link</span>}{detail.enriched_profile?.has_grant_data && <span className="status-badge">Observed grant data</span>}</div>
              <div className="registry-detail-grid">
                <div><span>Income</span><strong>{formatCurrency(detail.income)}</strong><small>{detail.financial_period_end_date ? `Reporting period ended ${detail.financial_period_end_date}` : "Reporting period not reported"}</small></div>
                <div><span>Expenditure</span><strong>{formatCurrency(detail.expenditure)}</strong><small>Charity Commission source value</small></div>
                <div><span>Registered office</span><strong>{detail.address_lines.concat(detail.postcode || "").filter(Boolean).join(", ") || "Not reported"}</strong><small>Registered office only; not beneficiary geography.</small></div>
                <div><span>Registry source</span><strong>{detail.source_name}</strong><small>{detail.source_record_updated_at ? `Source extract ${detail.source_record_updated_at}` : "Source date not reported"}</small></div>
              </div>
              <div className="registry-observed-note">{detail.observed_grant_data_message}</div>
              {(!detail.enriched_profile || registryEnrichment) && <div className={`registry-enrichment${registryEnrichment ? ` is-${registryEnrichment.status}` : ""}`}>
                <div className="registry-enrichment-heading">
                  <div>
                    <span>Profile and grant check</span>
                    <strong>{detail.enriched_profile ? "Organization profile available" : "Check 360Giving grants and create a profile"}</strong>
                    <small>{detail.enriched_profile
                      ? "This official record is now linked to an Organization Research profile."
                      : "Creates a profile from this cached official record and links already observed grants with the exact Charity Commission identifier."}</small>
                  </div>
                  {!detail.enriched_profile && <button
                    type="button"
                    className="btn btn-primary"
                    disabled={!online || registryEnrichment?.status === "running" || registryEnrichment?.status === "starting"}
                    onClick={() => void startRegistryEnrichment()}
                  >
                    {registryEnrichment?.status === "running" || registryEnrichment?.status === "starting" ? <LoaderCircle className="spin" size={16} /> : <ArrowRight size={16} />}
                    {registryEnrichment?.status === "running" || registryEnrichment?.status === "starting" ? "Adding…" : "Add profile"}
                  </button>}
                </div>
                {registryEnrichment && <div className="registry-enrichment-progress" role="status" aria-live="polite">
                  <div className="registry-enrichment-progress-copy">
                    <span>{registryEnrichment.message}</span>
                    <strong>{registryEnrichment.progress}%</strong>
                  </div>
                  <div
                    className="registry-enrichment-progress-track"
                    role="progressbar"
                    aria-label="Profile and grant check progress"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={registryEnrichment.progress}
                  ><i style={{ width: `${registryEnrichment.progress}%` }} /></div>
                  {registryEnrichment.error && <small className="registry-enrichment-error">{registryEnrichment.error}</small>}
                </div>}
              </div>}
              {detail.activity_text && <div className="registry-activity"><span>Registered activities</span><p>{detail.activity_text}</p></div>}
              {detail.enriched_profile && <div className="registry-enriched-link"><div><span>Enriched platform profile</span><strong>{detail.enriched_profile.organization_name}</strong><small>{detail.enriched_profile.match_method.replaceAll("_", " ")} · confidence {detail.enriched_profile.match_confidence ?? "not scored"}</small></div><button type="button" className="btn btn-primary" onClick={() => { setSelectedRegistryId(null); onOpenEnrichedProfile(detail.enriched_profile!.enriched_organization_id, detail.enriched_profile!.organization_name); }}>Open enriched profile <ArrowRight size={15} /></button></div>}
            </>}
          </section>
        </div>
      )}
    </section>
  );
}
