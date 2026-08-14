export type GrantScope = {
  beneficiaryCountry?: string;
  beneficiaryGeographies: string[];
  programmeAreas: string[];
  dateFrom?: string;
  dateTo?: string;
  currency?: string;
  donor?: string;
  recipient?: string;
  sources: string[];
};

export type DonorDirectoryState = {
  search: string;
  status: "all" | "linked" | "observed_only";
  sort: "largest_observed_funding" | "most_grants" | "most_recently_active";
  page: number;
  donorKey?: string;
};

export const DEFAULT_DONOR_DIRECTORY_STATE: DonorDirectoryState = {
  search: "",
  status: "all",
  sort: "largest_observed_funding",
  page: 1,
};

const URL_KEYS = {
  beneficiaryCountry: "funder_country",
  beneficiaryGeographies: "grant_geo",
  programmeAreas: "grant_programme",
  dateFrom: "grant_from",
  dateTo: "grant_to",
  currency: "grant_currency",
  donor: "grant_donor",
  recipient: "grant_recipient",
  sources: "grant_sources",
  search: "donor_search",
  status: "donor_status",
  sort: "funder_sort",
  page: "funder_page",
  donorKey: "donor",
} as const;

function unique(values: string[]): string[] {
  return Array.from(new Set(values.map(value => value.trim()).filter(Boolean)));
}

function csv(value: string | null): string[] {
  return unique((value || "").split(","));
}

function isoCountry(value: string | null): string | undefined {
  const normalized = String(value || "").trim().toUpperCase();
  return /^[A-Z]{2}$/.test(normalized) ? normalized : undefined;
}

function isoDate(value: string | null): string | undefined {
  const normalized = String(value || "").trim();
  return /^\d{4}-\d{2}-\d{2}$/.test(normalized) ? normalized : undefined;
}

export function emptyGrantScope(sources: string[] = []): GrantScope {
  return {
    beneficiaryGeographies: [],
    programmeAreas: [],
    sources: unique(sources),
  };
}

export function normalizeGrantScope(scope: GrantScope): GrantScope {
  const beneficiaryCountry = isoCountry(scope.beneficiaryCountry || null);
  const dateFrom = isoDate(scope.dateFrom || null);
  const dateTo = isoDate(scope.dateTo || null);
  const currency = String(scope.currency || "").trim().toUpperCase();
  return {
    ...(beneficiaryCountry ? { beneficiaryCountry } : {}),
    beneficiaryGeographies: unique(scope.beneficiaryGeographies),
    programmeAreas: unique(scope.programmeAreas),
    ...(dateFrom ? { dateFrom } : {}),
    ...(dateTo ? { dateTo } : {}),
    ...(currency && currency !== "AUTO" ? { currency } : {}),
    ...(String(scope.donor || "").trim() ? { donor: String(scope.donor).trim() } : {}),
    ...(String(scope.recipient || "").trim() ? { recipient: String(scope.recipient).trim() } : {}),
    sources: unique(scope.sources),
  };
}

export function grantScopeFromUrl(
  input: string | URLSearchParams = window.location.search,
  defaultSources: string[] = [],
): GrantScope {
  const params = typeof input === "string" ? new URLSearchParams(input) : input;
  const sourceScope = params.has(URL_KEYS.sources)
    ? csv(params.get(URL_KEYS.sources))
    : unique(defaultSources);
  return normalizeGrantScope({
    beneficiaryCountry: isoCountry(params.get(URL_KEYS.beneficiaryCountry)),
    beneficiaryGeographies: csv(params.get(URL_KEYS.beneficiaryGeographies)),
    programmeAreas: csv(params.get(URL_KEYS.programmeAreas)),
    dateFrom: isoDate(params.get(URL_KEYS.dateFrom)),
    dateTo: isoDate(params.get(URL_KEYS.dateTo)),
    currency: params.get(URL_KEYS.currency) || undefined,
    donor: params.get(URL_KEYS.donor) || undefined,
    recipient: params.get(URL_KEYS.recipient) || undefined,
    sources: sourceScope,
  });
}

function setOrDelete(params: URLSearchParams, key: string, value?: string): void {
  if (value) params.set(key, value);
  else params.delete(key);
}

export function applyGrantScopeToParams(
  params: URLSearchParams,
  scope: GrantScope,
  options: { includeCountry?: boolean; persistEmptySources?: boolean } = {},
): URLSearchParams {
  const normalized = normalizeGrantScope(scope);
  if (options.includeCountry !== false) {
    setOrDelete(params, URL_KEYS.beneficiaryCountry, normalized.beneficiaryCountry);
  }
  setOrDelete(params, URL_KEYS.beneficiaryGeographies, normalized.beneficiaryGeographies.join(","));
  setOrDelete(params, URL_KEYS.programmeAreas, normalized.programmeAreas.join(","));
  setOrDelete(params, URL_KEYS.dateFrom, normalized.dateFrom);
  setOrDelete(params, URL_KEYS.dateTo, normalized.dateTo);
  setOrDelete(params, URL_KEYS.currency, normalized.currency && normalized.currency !== "AUTO" ? normalized.currency : undefined);
  setOrDelete(params, URL_KEYS.donor, normalized.donor);
  setOrDelete(params, URL_KEYS.recipient, normalized.recipient);
  if (normalized.sources.length || options.persistEmptySources) {
    params.set(URL_KEYS.sources, normalized.sources.join(","));
  } else {
    params.delete(URL_KEYS.sources);
  }
  return params;
}

export function grantScopeToApiParams(
  scope: GrantScope,
  options: { requireCountry?: boolean } = {},
): URLSearchParams {
  const normalized = normalizeGrantScope(scope);
  const params = new URLSearchParams();
  if (normalized.beneficiaryCountry) {
    params.set("beneficiary_country", normalized.beneficiaryCountry);
  } else if (options.requireCountry) {
    throw new Error("A beneficiary country is required for the Donor Directory.");
  }
  if (normalized.currency) params.set("currency", normalized.currency);
  if (normalized.dateFrom) params.set("date_from", normalized.dateFrom);
  if (normalized.dateTo) params.set("date_to", normalized.dateTo);
  if (normalized.beneficiaryGeographies.length) params.set("beneficiary_geographies", normalized.beneficiaryGeographies.join(","));
  if (normalized.programmeAreas.length) params.set("programme_areas", normalized.programmeAreas.join(","));
  if (normalized.donor) params.set("donor", normalized.donor);
  if (normalized.recipient) params.set("recipient", normalized.recipient);
  params.set("sources", normalized.sources.join(","));
  return params;
}

export function donorDirectoryStateFromUrl(
  input: string | URLSearchParams = window.location.search,
): DonorDirectoryState {
  const params = typeof input === "string" ? new URLSearchParams(input) : input;
  const rawStatus = params.get(URL_KEYS.status);
  const rawSort = params.get(URL_KEYS.sort);
  const rawPage = Number(params.get(URL_KEYS.page) || "1");
  const donorKey = params.get(URL_KEYS.donorKey) || undefined;
  return {
    search: (params.get(URL_KEYS.search) || "").slice(0, 160),
    status: rawStatus === "linked" || rawStatus === "observed_only" ? rawStatus : "all",
    sort: rawSort === "most_grants" || rawSort === "most_active"
      ? "most_grants"
      : rawSort === "most_recently_active" || rawSort === "most_recent"
        ? "most_recently_active"
        : "largest_observed_funding",
    page: Number.isFinite(rawPage) && rawPage > 0 ? Math.floor(rawPage) : 1,
    ...(donorKey ? { donorKey } : {}),
  };
}

export function applyDonorDirectoryStateToParams(
  params: URLSearchParams,
  state: DonorDirectoryState,
): URLSearchParams {
  setOrDelete(params, URL_KEYS.search, state.search.trim());
  setOrDelete(params, URL_KEYS.status, state.status === "all" ? undefined : state.status);
  setOrDelete(params, URL_KEYS.sort, state.sort === "largest_observed_funding" ? undefined : state.sort);
  setOrDelete(params, URL_KEYS.page, state.page > 1 ? String(state.page) : undefined);
  setOrDelete(params, URL_KEYS.donorKey, state.donorKey);
  return params;
}

export function replaceCurrentUrl(
  scope: GrantScope,
  directoryState?: DonorDirectoryState,
  mode: "push" | "replace" = "replace",
): void {
  const params = applyGrantScopeToParams(
    new URLSearchParams(window.location.search),
    scope,
    { persistEmptySources: true },
  );
  if (directoryState) applyDonorDirectoryStateToParams(params, directoryState);
  const query = params.toString();
  window.history[mode === "push" ? "pushState" : "replaceState"](
    {},
    "",
    `${window.location.pathname}${query ? `?${query}` : ""}`,
  );
}

export type GrantScopeChip = {
  key: keyof GrantScope;
  label: string;
};

export function grantScopeChips(scope: GrantScope): GrantScopeChip[] {
  const normalized = normalizeGrantScope(scope);
  const chips: GrantScopeChip[] = [];
  if (normalized.beneficiaryCountry) chips.push({ key: "beneficiaryCountry", label: `Beneficiary: ${normalized.beneficiaryCountry}` });
  if (normalized.dateFrom || normalized.dateTo) chips.push({ key: "dateFrom", label: `${normalized.dateFrom || "Start"} – ${normalized.dateTo || "Today"}` });
  normalized.programmeAreas.forEach(value => chips.push({ key: "programmeAreas", label: value }));
  normalized.beneficiaryGeographies.forEach(value => chips.push({ key: "beneficiaryGeographies", label: value }));
  if (normalized.currency && normalized.currency !== "AUTO") chips.push({ key: "currency", label: `Currency: ${normalized.currency}` });
  if (normalized.donor) chips.push({ key: "donor", label: `Donor: ${normalized.donor}` });
  if (normalized.recipient) chips.push({ key: "recipient", label: `Recipient: ${normalized.recipient}` });
  return chips;
}

export function grantScopeFilterCount(scope: GrantScope): number {
  return grantScopeChips({ ...scope, beneficiaryCountry: undefined }).length;
}

export function removeGrantScopeValue(scope: GrantScope, key: keyof GrantScope, value?: string): GrantScope {
  if (key === "programmeAreas" || key === "beneficiaryGeographies") {
    return normalizeGrantScope({
      ...scope,
      [key]: value ? scope[key].filter(item => item !== value) : [],
    });
  }
  if (key === "sources") return normalizeGrantScope({ ...scope, sources: [] });
  if (key === "dateFrom" || key === "dateTo") {
    return normalizeGrantScope({ ...scope, dateFrom: undefined, dateTo: undefined });
  }
  return normalizeGrantScope({ ...scope, [key]: undefined });
}

export function grantScopesEqual(left: GrantScope, right: GrantScope): boolean {
  return JSON.stringify(normalizeGrantScope(left)) === JSON.stringify(normalizeGrantScope(right));
}
