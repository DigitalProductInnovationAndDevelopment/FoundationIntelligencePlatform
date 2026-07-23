import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Building2, MoreHorizontal, Search, SlidersHorizontal, X } from "lucide-react";
import worldMap from "@svg-maps/world";

export interface MapRankingItem {
  name: string;
  count: number;
}

export interface GrantMapItem {
  region_or_country_code: string | null;
  region_or_country_name: string;
  grant_count: number;
  total_amount: number | null;
  currency: string | null;
  distinct_funders: number;
  distinct_recipients: number;
  top_programme_areas: MapRankingItem[];
  top_funders: MapRankingItem[];
  top_recipients: MapRankingItem[];
  original_geographies: string[];
  funding_grant_count: number;
  excluded_multi_country_grant_count: number;
  excluded_invalid_amount_grant_count: number;
}

export interface GrantMapConnection {
  origin_country_code: string;
  origin_country_name: string;
  destination_country_code: string;
  destination_country_name: string;
  grant_count: number;
  top_funders: MapRankingItem[];
  origin_sources: string[];
}

export interface GrantMapFilters {
  search: string;
  tags: string[];
  foundationRegions: string[];
  fundingRegions: string[];
  minAnnualGiving: number;
  minAvgGrantSize: number;
}

export interface GrantMapFilterOptions {
  sectors: { value: string; label: string }[];
  headquartersLocations: string[];
  beneficiaryGeographies: string[];
  annualGivingSteps: number[];
  annualGivingLabels: string[];
  averageGrantSteps: number[];
  averageGrantLabels: string[];
}

export interface GrantMapResponse {
  status: string;
  geographic_dimension: string;
  items: GrantMapItem[];
  known_geography_count: number;
  unknown_geography_count: number;
  coverage_percentage: number;
  currencies: string[];
  selected_currency: string | null;
  funding_status: string;
  funding_mode_available: boolean;
  grant_country_association_count: number;
  multi_country_grant_count: number;
  funding_excluded_multi_country_count: number;
  funding_excluded_multi_country_amount: number;
  funding_excluded_currency_count: number;
  funding_excluded_invalid_amount_count: number;
  connections: GrantMapConnection[];
  connection_grant_count: number;
  connection_excluded_no_headquarters_count: number;
  connection_same_country_count: number;
  minimum_coverage_threshold: number;
  metadata: {
    data_mode: string;
    source: string[];
    record_count: number;
    coverage?: number | null;
    limitations: string[];
  };
}

interface GrantWorldMapProps {
  data: GrantMapResponse;
  loading: boolean;
  error: string | null;
  filters: GrantMapFilters;
  filterOptions: GrantMapFilterOptions;
  onFiltersChange: (filters: GrantMapFilters) => void;
  onOpenOrganizationDirectory: (filters: GrantMapFilters) => void;
}

type MapMetric = "count" | "funding";

interface SvgMapLocation {
  id: string;
  name: string;
  path: string;
}

interface ConnectionGeometry {
  connection: GrantMapConnection;
  path: string;
  strokeWidth: number;
  opacity: number;
}

const MAP_COLORS = ["#ede9fe", "#ddd6fe", "#c4b5fd", "#a78bfa", "#7c3aed"];
const MAX_VISIBLE_CONNECTIONS = 36;

function compactNumber(value: number) {
  return new Intl.NumberFormat("en-GB", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

function currencyAmount(value: number | null, currency: string | null) {
  if (value === null || !currency) return "Unavailable";
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency,
    notation: value >= 1_000_000 ? "compact" : "standard",
    maximumFractionDigits: value >= 1_000_000 ? 1 : 0,
  }).format(value);
}

function quantile(sortedValues: number[], percentile: number) {
  if (!sortedValues.length) return 0;
  const index = Math.min(
    sortedValues.length - 1,
    Math.max(0, Math.ceil(percentile * sortedValues.length) - 1),
  );
  return sortedValues[index];
}

function buildQuantileScale(values: number[]) {
  const sorted = values.filter(value => Number.isFinite(value) && value >= 0).sort((a, b) => a - b);
  const thresholds = [0.2, 0.4, 0.6, 0.8].map(percentile => quantile(sorted, percentile));
  return {
    thresholds,
    color(value: number) {
      const bucket = thresholds.findIndex(threshold => value <= threshold);
      return MAP_COLORS[bucket === -1 ? MAP_COLORS.length - 1 : bucket];
    },
  };
}

function RankingList({ items }: { items: MapRankingItem[] }) {
  if (!items.length) return <span className="map-empty-value">Unavailable</span>;
  return (
    <ul className="map-ranking-list">
      {items.map(item => (
        <li key={item.name}>
          <span>{item.name}</span>
          <strong>{item.count}</strong>
        </li>
      ))}
    </ul>
  );
}

function FilterChecklist({
  label,
  options,
  selected,
  onChange,
}: {
  label: string;
  options: { value: string; label: string }[];
  selected: string[];
  onChange: (values: string[]) => void;
}) {
  return (
    <fieldset className="map-settings-section">
      <legend>{label}</legend>
      <div className="map-settings-checklist">
        {options.map(option => (
          <label key={option.value}>
            <input
              type="checkbox"
              checked={selected.includes(option.value)}
              onChange={event => onChange(
                event.target.checked
                  ? [...selected, option.value]
                  : selected.filter(value => value !== option.value),
              )}
            />
            <span>{option.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export default function GrantWorldMap({
  data,
  loading,
  error,
  filters,
  filterOptions,
  onFiltersChange,
  onOpenOrganizationDirectory,
}: GrantWorldMapProps) {
  const [metric, setMetric] = useState<MapMetric>("count");
  const [hoveredCode, setHoveredCode] = useState<string | null>(null);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [draftFilters, setDraftFilters] = useState<GrantMapFilters>(filters);
  const [showConnections, setShowConnections] = useState(false);
  const [connectionGeometry, setConnectionGeometry] = useState<ConnectionGeometry[]>([]);
  const controlsRef = useRef<HTMLDivElement>(null);
  const pathRefs = useRef(new Map<string, SVGPathElement>());
  const fundingAvailable = data.funding_mode_available;
  const activeMetric: MapMetric = metric === "funding" && !fundingAvailable ? "count" : metric;
  const countLabel = data.multi_country_grant_count
    ? "Grant-country associations"
    : "Number of grants";

  useEffect(() => setDraftFilters(filters), [filters]);

  useEffect(() => {
    if (!settingsOpen) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (controlsRef.current && !controlsRef.current.contains(event.target as Node)) {
        setSettingsOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSettingsOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [settingsOpen]);

  const visibleConnections = useMemo(
    () => (data.connections || []).slice(0, MAX_VISIBLE_CONNECTIONS),
    [data.connections],
  );

  useLayoutEffect(() => {
    if (!showConnections || !visibleConnections.length) {
      setConnectionGeometry([]);
      return;
    }
    let animationFrame = 0;
    const calculateGeometry = () => {
      const maximum = Math.max(...visibleConnections.map(item => item.grant_count), 1);
      const geometry = visibleConnections.flatMap((connection, index) => {
        const origin = pathRefs.current.get(connection.origin_country_code.toUpperCase());
        const destination = pathRefs.current.get(connection.destination_country_code.toUpperCase());
        if (!origin || !destination) return [];
        try {
          const originBox = origin.getBBox();
          const destinationBox = destination.getBBox();
          const x1 = originBox.x + originBox.width / 2;
          const y1 = originBox.y + originBox.height / 2;
          const x2 = destinationBox.x + destinationBox.width / 2;
          const y2 = destinationBox.y + destinationBox.height / 2;
          const dx = x2 - x1;
          const dy = y2 - y1;
          const distance = Math.max(Math.hypot(dx, dy), 1);
          const direction = index % 2 === 0 ? 1 : -1;
          const bend = Math.min(62, distance * 0.16) * direction;
          const controlX = (x1 + x2) / 2 - (dy / distance) * bend;
          const controlY = (y1 + y2) / 2 + (dx / distance) * bend;
          const strength = Math.sqrt(connection.grant_count / maximum);
          return [{
            connection,
            path: `M ${x1} ${y1} Q ${controlX} ${controlY} ${x2} ${y2}`,
            strokeWidth: 0.9 + strength * 2.5,
            opacity: 0.28 + strength * 0.48,
          }];
        } catch {
          return [];
        }
      });
      setConnectionGeometry(geometry);
    };
    animationFrame = window.requestAnimationFrame(calculateGeometry);
    window.addEventListener("resize", calculateGeometry);
    return () => {
      window.cancelAnimationFrame(animationFrame);
      window.removeEventListener("resize", calculateGeometry);
    };
  }, [showConnections, visibleConnections]);

  const itemByCode = useMemo(() => new Map(
    data.items
      .filter(item => item.region_or_country_code)
      .map(item => [String(item.region_or_country_code).toUpperCase(), item]),
  ), [data.items]);

  const values = useMemo(() => data.items
    .map(item => activeMetric === "count" ? item.grant_count : item.total_amount)
    .filter((value): value is number => value !== null && Number.isFinite(value)),
  [activeMetric, data.items]);
  const scale = useMemo(() => buildQuantileScale(values), [values]);

  const activeItem = (
    hoveredCode ? itemByCode.get(hoveredCode) : undefined
  ) || (
    selectedCode ? itemByCode.get(selectedCode) : undefined
  );
  const selectedItem = selectedCode ? itemByCode.get(selectedCode) : undefined;
  const totalFiltered = data.known_geography_count + data.unknown_geography_count;
  const isMapAvailable = ["available", "low_coverage"].includes(data.status) && data.items.length > 0;

  const legendLabels = scale.thresholds.map((threshold, index) => {
    const formatter = activeMetric === "funding"
      ? (value: number) => currencyAmount(value, data.selected_currency)
      : compactNumber;
    if (index === 0) return `Up to ${formatter(threshold)}`;
    return `${formatter(scale.thresholds[index - 1])}–${formatter(threshold)}`;
  });
  if (values.length) {
    const formatter = activeMetric === "funding"
      ? (value: number) => currencyAmount(value, data.selected_currency)
      : compactNumber;
    legendLabels.push(`More than ${formatter(scale.thresholds.at(-1) || 0)}`);
  }

  const selectCountry = (code: string) => {
    setSelectedCode(previous => previous === code ? null : code);
  };

  const activeFilterCount = (
    (filters.search.trim() ? 1 : 0)
    + filters.tags.length
    + filters.foundationRegions.length
    + filters.fundingRegions.length
    + (filters.minAnnualGiving > 0 ? 1 : 0)
    + (filters.minAvgGrantSize > 0 ? 1 : 0)
  );
  const annualGivingIndex = Math.max(
    0,
    filterOptions.annualGivingSteps.indexOf(draftFilters.minAnnualGiving),
  );
  const averageGrantIndex = Math.max(
    0,
    filterOptions.averageGrantSteps.indexOf(draftFilters.minAvgGrantSize),
  );
  const resetFilters = () => {
    const emptyFilters: GrantMapFilters = {
      search: "",
      tags: [],
      foundationRegions: [],
      fundingRegions: [],
      minAnnualGiving: 0,
      minAvgGrantSize: 0,
    };
    setDraftFilters(emptyFilters);
    onFiltersChange(emptyFilters);
  };

  return (
    <section className="glass-card world-map-card" aria-labelledby="global-grant-map-title">
      <div className="world-map-header">
        <div>
          <h3 id="global-grant-map-title">Global Grant Distribution</h3>
          <p>
            Beneficiary geography in the real 360Giving grants currently ingested into this application.
            This is not complete 360Giving or global-market coverage.
          </p>
        </div>
        <div className="map-controls-anchor" ref={controlsRef}>
          <div className="map-mode-control" role="group" aria-label="World map display and settings">
            <button
              type="button"
              className={activeMetric === "count" ? "active" : ""}
              aria-pressed={activeMetric === "count"}
              onClick={() => setMetric("count")}
            >
              {countLabel}
            </button>
            <button
              type="button"
              className={activeMetric === "funding" ? "active" : ""}
              aria-pressed={activeMetric === "funding"}
              disabled={!fundingAvailable}
              title={!fundingAvailable ? "Select one available currency before comparing awarded funding." : undefined}
              onClick={() => setMetric("funding")}
            >
              Awarded funding{data.selected_currency ? ` (${data.selected_currency})` : ""}
            </button>
            <span className="map-mode-divider" aria-hidden="true" />
            <button
              type="button"
              className={`map-settings-trigger${settingsOpen ? " active" : ""}`}
              aria-label={`Map settings${activeFilterCount ? `, ${activeFilterCount} active filters` : ""}`}
              aria-expanded={settingsOpen}
              aria-controls="grant-map-settings"
              aria-haspopup="dialog"
              onClick={() => setSettingsOpen(previous => !previous)}
            >
              <MoreHorizontal size={19} />
              {activeFilterCount > 0 && <span>{activeFilterCount}</span>}
            </button>
          </div>

          {settingsOpen && (
            <section
              id="grant-map-settings"
              className="map-settings-popover"
              role="dialog"
              aria-modal="false"
              aria-labelledby="grant-map-settings-title"
            >
              <div className="map-settings-header">
                <div>
                  <span><SlidersHorizontal size={14} /> Map controls</span>
                  <h4 id="grant-map-settings-title">Filter grant distribution</h4>
                </div>
                <button type="button" onClick={() => setSettingsOpen(false)} aria-label="Close map settings">
                  <X size={17} />
                </button>
              </div>

              <div className="map-settings-scroll">
                <label className="map-settings-search">
                  <span>Funder organization</span>
                  <div>
                    <Search size={15} />
                    <input
                      type="search"
                      value={draftFilters.search}
                      placeholder="Search organization name…"
                      onChange={event => setDraftFilters(current => ({
                        ...current,
                        search: event.target.value,
                      }))}
                    />
                  </div>
                </label>

                <FilterChecklist
                  label="Thematic sector"
                  options={filterOptions.sectors}
                  selected={draftFilters.tags}
                  onChange={tags => setDraftFilters(current => ({ ...current, tags }))}
                />
                <FilterChecklist
                  label="Foundation location"
                  options={filterOptions.headquartersLocations.map(value => ({ value, label: value }))}
                  selected={draftFilters.foundationRegions}
                  onChange={foundationRegions => setDraftFilters(current => ({
                    ...current,
                    foundationRegions,
                  }))}
                />
                <FilterChecklist
                  label="Beneficiary geography"
                  options={filterOptions.beneficiaryGeographies.map(value => ({ value, label: value }))}
                  selected={draftFilters.fundingRegions}
                  onChange={fundingRegions => setDraftFilters(current => ({
                    ...current,
                    fundingRegions,
                  }))}
                />

                <label className="map-settings-range">
                  <span><b>Minimum annual giving</b><strong>{filterOptions.annualGivingLabels[annualGivingIndex]}</strong></span>
                  <input
                    type="range"
                    min="0"
                    max={filterOptions.annualGivingSteps.length - 1}
                    value={annualGivingIndex}
                    onChange={event => setDraftFilters(current => ({
                      ...current,
                      minAnnualGiving: filterOptions.annualGivingSteps[Number(event.target.value)],
                    }))}
                  />
                </label>

                <label className="map-settings-range">
                  <span><b>Minimum average grant</b><strong>{filterOptions.averageGrantLabels[averageGrantIndex]}</strong></span>
                  <input
                    type="range"
                    min="0"
                    max={filterOptions.averageGrantSteps.length - 1}
                    value={averageGrantIndex}
                    onChange={event => setDraftFilters(current => ({
                      ...current,
                      minAvgGrantSize: filterOptions.averageGrantSteps[Number(event.target.value)],
                    }))}
                  />
                </label>

                <label className="map-settings-toggle">
                  <span>
                    <b>Connection arrows</b>
                    <small>Registered funder location → beneficiary country</small>
                  </span>
                  <input
                    type="checkbox"
                    role="switch"
                    checked={showConnections}
                    onChange={event => setShowConnections(event.target.checked)}
                  />
                </label>
                <p className="map-settings-disclosure">
                  Arrows are illustrative associations, not verified financial routes. Up to the
                  {` ${MAX_VISIBLE_CONNECTIONS} `}strongest country connections are shown.
                </p>
              </div>

              <div className="map-settings-footer">
                <button type="button" className="map-settings-reset" onClick={resetFilters}>Reset filters</button>
                <button
                  type="button"
                  className="map-settings-apply"
                  onClick={() => {
                    onFiltersChange(draftFilters);
                    setSettingsOpen(false);
                  }}
                >
                  Apply filters
                </button>
              </div>
            </section>
          )}
        </div>
      </div>

      <div className="map-coverage-grid" aria-label="Map data coverage">
        <div><strong>{totalFiltered.toLocaleString("en-GB")}</strong><span>{activeFilterCount ? "Filtered grants" : "Ingested grants"}</span></div>
        <div><strong>{data.known_geography_count.toLocaleString("en-GB")}</strong><span>Mapped grants</span></div>
        <div><strong>{data.unknown_geography_count.toLocaleString("en-GB")}</strong><span>Unmapped grants</span></div>
        <div><strong>{data.coverage_percentage}%</strong><span>Country coverage</span></div>
      </div>

      {loading ? (
        <div className="world-map-state" role="status"><div className="spinner" /><span>Loading beneficiary geography…</span></div>
      ) : error ? (
        <div className="world-map-state data-notice data-notice-warning" role="alert">{error}</div>
      ) : (
        <>
          {!isMapAvailable && (
            <div className="data-notice data-notice-warning map-inline-notice" role="status">
              {data.status === "low_coverage"
                ? `Only ${data.coverage_percentage}% of filtered grants have usable country geography. Values are withheld below the ${Math.round(data.minimum_coverage_threshold * 100)}% coverage threshold.`
                : data.status === "no_geography"
                  ? "The current grants contain no beneficiary geography that can be resolved to a country."
                  : "Beneficiary-country data is unavailable for the current grant scope."}
            </div>
          )}

          <div className="world-map-stage">
            <svg
              className="world-map-svg"
              viewBox={worldMap.viewBox}
              role="group"
              aria-labelledby="global-grant-map-title global-grant-map-description"
              preserveAspectRatio="xMidYMid meet"
            >
              <desc id="global-grant-map-description">
                World map showing {data.known_geography_count} of {totalFiltered} currently ingested grants with
                resolvable beneficiary-country geography. Countries without a displayed value may be outside the
                currently ingested sources or have unresolved geography.
              </desc>
              <defs>
                <marker
                  id="grant-map-arrowhead"
                  viewBox="0 0 10 10"
                  refX="8"
                  refY="5"
                  markerWidth="5"
                  markerHeight="5"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 0 L 10 5 L 0 10 z" className="map-connection-arrowhead" />
                </marker>
              </defs>
              {(worldMap.locations as SvgMapLocation[]).map(location => {
                const code = location.id.toUpperCase();
                const item = itemByCode.get(code);
                const value = item
                  ? activeMetric === "count" ? item.grant_count : item.total_amount
                  : null;
                const selected = selectedCode === code;
                const ariaValue = item
                  ? activeMetric === "count"
                    ? `${item.grant_count} grant-country associations`
                    : currencyAmount(item.total_amount, data.selected_currency)
                  : "no mapped value in the currently ingested grants";
                return (
                  <path
                    key={location.id}
                    ref={node => {
                      if (node) pathRefs.current.set(code, node);
                      else pathRefs.current.delete(code);
                    }}
                    d={location.path}
                    className={`world-map-country${item ? " has-data" : ""}${selected ? " selected" : ""}`}
                    style={{ fill: item && value !== null ? scale.color(value) : undefined }}
                    role={item ? "button" : undefined}
                    tabIndex={item ? 0 : undefined}
                    aria-label={item ? `${item.region_or_country_name}: ${ariaValue}. Open country summary.` : undefined}
                    aria-pressed={item ? selected : undefined}
                    onMouseEnter={() => item && setHoveredCode(code)}
                    onMouseLeave={() => setHoveredCode(null)}
                    onFocus={() => item && setHoveredCode(code)}
                    onBlur={() => setHoveredCode(null)}
                    onClick={() => item && selectCountry(code)}
                    onKeyDown={event => {
                      if (item && (event.key === "Enter" || event.key === " ")) {
                        event.preventDefault();
                        selectCountry(code);
                      }
                    }}
                  >
                    <title>{item ? `${item.region_or_country_name}: ${ariaValue}` : `${location.name}: no mapped value in current data`}</title>
                  </path>
                );
              })}
              {showConnections && (
                <g className="map-connection-layer" aria-label="Illustrative funder headquarters to beneficiary country connections">
                  {connectionGeometry.map(({ connection, path, strokeWidth, opacity }) => (
                    <path
                      key={`${connection.origin_country_code}-${connection.destination_country_code}`}
                      d={path}
                      className="map-connection-line"
                      style={{ strokeWidth, opacity }}
                      markerEnd="url(#grant-map-arrowhead)"
                    >
                      <title>
                        {connection.origin_country_name} to {connection.destination_country_name}: {connection.grant_count} grant association(s). Illustrative registered-location connection, not a verified financial route.
                      </title>
                    </path>
                  ))}
                </g>
              )}
            </svg>

            {showConnections && (
              <div className="map-connection-disclosure" role="note">
                <strong>{connectionGeometry.length} illustrative connections</strong>
                <span>
                  Registered funder location → beneficiary country · {data.connection_grant_count.toLocaleString("en-GB")} grants with drawable routes
                </span>
              </div>
            )}

            <aside className="map-hover-summary" aria-live="polite">
              {activeItem ? (
                <>
                  <span className="map-hover-eyebrow">Beneficiary country</span>
                  <strong>{activeItem.region_or_country_name}</strong>
                  <div><span>Grants</span><b>{activeItem.grant_count.toLocaleString("en-GB")}</b></div>
                  <div><span>Awarded funding</span><b>{currencyAmount(activeItem.total_amount, activeItem.currency)}</b></div>
                  <div><span>Funders</span><b>{activeItem.distinct_funders}</b></div>
                  <div><span>Recipients</span><b>{activeItem.distinct_recipients}</b></div>
                  {activeItem.excluded_multi_country_grant_count > 0 && (
                    <small>{activeItem.excluded_multi_country_grant_count} multi-country grant amount(s) excluded.</small>
                  )}
                </>
              ) : (
                <>
                  <span className="map-hover-eyebrow">Explore the map</span>
                  <strong>Point to a shaded country</strong>
                  <p>Hover, focus, or tap for a summary. Select a country to open the detail panel.</p>
                </>
              )}
            </aside>
          </div>

          {values.length > 0 && (
            <div className="map-legend" aria-label={`Quantile legend for ${activeMetric === "count" ? countLabel : "awarded funding"}`}>
              <span className="map-legend-title">Quantile scale</span>
              {legendLabels.map((label, index) => (
                <span className="map-legend-item" key={`${label}-${index}`}>
                  <i style={{ background: MAP_COLORS[index] }} />{label}
                </span>
              ))}
              <span className="map-legend-item"><i className="no-data" />No mapped value</span>
            </div>
          )}

          <div className="map-coverage-note">
            <span>
              {data.multi_country_grant_count > 0
                ? `${data.multi_country_grant_count} multi-country grants count once per associated country. Their full amounts are excluded from country funding totals.`
                : "Each mapped grant is associated with one resolved beneficiary country."}
            </span>
            {data.funding_excluded_multi_country_count > 0 && data.selected_currency && (
              <span>
                Excluded multi-country amount: {currencyAmount(data.funding_excluded_multi_country_amount, data.selected_currency)}.
              </span>
            )}
          </div>

          {selectedItem && (
            <section className="country-explorer" aria-labelledby="country-explorer-title">
              <div className="country-explorer-header">
                <div>
                  <span>Country summary</span>
                  <h4 id="country-explorer-title">{selectedItem.region_or_country_name}</h4>
                </div>
                <button type="button" className="country-explorer-close" onClick={() => setSelectedCode(null)} aria-label="Close country summary">
                  <X size={18} />
                </button>
              </div>
              <div className="country-explorer-metrics">
                <div><span>Mapped grants</span><strong>{selectedItem.grant_count}</strong></div>
                <div><span>Valid awarded funding</span><strong>{currencyAmount(selectedItem.total_amount, selectedItem.currency)}</strong></div>
                <div><span>Distinct funders</span><strong>{selectedItem.distinct_funders}</strong></div>
                <div><span>Distinct recipients</span><strong>{selectedItem.distinct_recipients}</strong></div>
              </div>
              <div className="country-explorer-columns">
                <div><h5>Leading programme areas</h5><RankingList items={selectedItem.top_programme_areas} /></div>
                <div><h5>Leading funders</h5><RankingList items={selectedItem.top_funders} /></div>
                <div><h5>Leading recipients</h5><RankingList items={selectedItem.top_recipients} /></div>
              </div>
              <p className="country-explorer-note">
                Source labels retained: {selectedItem.original_geographies.join(", ") || "Unavailable"}.
                {selectedItem.excluded_multi_country_grant_count > 0
                  ? ` ${selectedItem.excluded_multi_country_grant_count} multi-country grant amount(s) are excluded from this country total.`
                  : " No multi-country award amount is duplicated in this country total."}
              </p>
              <button
                type="button"
                className="country-explorer-directory-button"
                onClick={() => onOpenOrganizationDirectory({
                  ...filters,
                  fundingRegions: [selectedItem.region_or_country_name],
                })}
              >
                <Building2 size={18} />
                <span>
                  <strong>View matching organizations</strong>
                  <small>Open the directory with these filters and {selectedItem.region_or_country_name} selected</small>
                </span>
                <ArrowRight size={18} />
              </button>
            </section>
          )}

          <div className="map-attribution">
            Map geometry: <a href="https://github.com/VictorCazanave/svg-maps/tree/master/packages/world" target="_blank" rel="noreferrer">@svg-maps/world</a>, CC BY 4.0.
          </div>
        </>
      )}
    </section>
  );
}
