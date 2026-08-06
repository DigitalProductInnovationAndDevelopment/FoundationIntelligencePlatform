import {
  lazy,
  Suspense,
  useEffect,
  useEffectEvent,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Building2,
  ArrowLeft,
  TrendingUp,
  Activity,
  Search,
  Terminal,
  ArrowRight,
  TrendingDown,
  DollarSign,
  Download,
  Play,
  Newspaper,
  LoaderCircle,
  PanelLeftClose,
  PanelLeftOpen,
  Star,
  X,
} from "lucide-react";
import AppHeader from "./components/AppHeader";
import type {
  FavoriteGrantExplorerPayload,
  OverviewFilters,
} from "./components/OverviewDashboard";
import type {
  FavoriteDonorPayload,
  FavoriteDonorRequestPayload,
  HeaderContextState,
} from "./components/DonorDirectoryPage";
import {
  applyGrantScopeToParams,
  grantScopeFromUrl,
  type GrantScope,
} from "./lib/grantScope";
import { mutationHeaders } from "./lib/http";
import amplifyLogo from "./assets/amplify-logo.svg";
import type {
  GrantMapFilters,
  GrantMapResponse,
  SourceFunderCountrySelection,
} from "./components/GrantWorldMap";

const GrantWorldMap = lazy(() => import("./components/GrantWorldMap"));
const OverviewDashboard = lazy(() => import("./components/OverviewDashboard"));
const GrantAwardsChart = lazy(() => import("./components/DataCharts").then(module => ({ default: module.GrantAwardsChart })));
const ProgrammeAllocationChart = lazy(() => import("./components/DataCharts").then(module => ({ default: module.ProgrammeAllocationChart })));
const FinancialHistoryChart = lazy(() => import("./components/DataCharts").then(module => ({ default: module.FinancialHistoryChart })));
const RegistryDirectory = lazy(() => import("./components/RegistryDirectory"));
const DonorDirectoryPage = lazy(() => import("./components/DonorDirectoryPage"));

// Configuration for API requests
const API_BASE = import.meta.env.VITE_API_BASE_URL
  || "";
const SHOW_LEGACY_OVERVIEW = import.meta.env.VITE_LEGACY_OVERVIEW === "true";
const DEFAULT_DATA_SOURCES = ["360Giving", "Charity Commission for England and Wales", "Philea"];
// Interface definitions
interface Charity {
  registered_charity_number: number;
  suffix: number;
  link: string;
  charity_name: string;
  reg_status: string;
  reporting_status: string;
  removal_reason: string | null;
  latest_income: number | null;
  latest_expenditure: number | null;
  programme_areas_source?: string[];
  programme_areas_inferred?: string[];
  geographic_focus_source?: unknown[];
  geographic_focus_inferred?: string[];
  headquarters_country?: string | null;
  headquarters_region?: string | null;
  programme_area_review_required?: boolean;
  geography_review_required?: boolean;
  enrichment_rule_version?: string | null;
  organization_type?: string;
  primary_source?: string | null;
  source_names?: string[];
  source_record_id?: string | null;
  source_url?: string | null;
  transaction_coverage?: string;
  relevance_score?: number | null;
  score_confidence?: number | null;
  score_completeness?: number | null;
  score_target?: string | null;
  score_version?: string | null;
  score_configuration_status?: string | null;
}

type FavoriteProfile = {
  key: string;
  profile: Charity;
  savedAt: number;
};

type FavoriteResearchView = {
  key: string;
  label: string;
  filters: {
    searchTerm: string;
    selectedTags: string[];
    selectedFoundationRegions: string[];
    selectedRecipientRegions: string[];
    annualGivingIndex: number;
    maxAnnualGivingInput: string;
    avgGrantSizeIndex: number;
    maxAvgGrantSizeInput: string;
    profileSort: "score_desc" | "income_desc" | "name_asc";
  };
  savedAt: number;
};

type FavoritesState = {
  profiles: FavoriteProfile[];
  donors: FavoriteDonorPayload[];
  donorRequests: FavoriteDonorRequestPayload[];
  researchViews: FavoriteResearchView[];
  grantExplorers: FavoriteGrantExplorerPayload[];
};

type FavoriteDonorWorkspace =
  | { kind: "donor"; item: FavoriteDonorPayload }
  | { kind: "request"; item: FavoriteDonorRequestPayload };

type ActiveSourceFunder = {
  sourceFunderKey: string;
  displayName: string;
};

type NewsSourceItem = {
  title: string;
  link: string;
  source: string;
  published: string;
  note?: string;
};

type NewsSummaryPayload = {
  foundation: string;
  summary: string;
  sources: NewsSourceItem[];
  searched_weeks: number;
  generated_at: string;
};

type NewsProgressStep = "discovering" | "reading" | "summarizing";

type SavedNewsRun = NewsSummaryPayload & {
  organizationKey: string;
  savedAt: string;
  mode: "live" | "illustrative";
};

const FAVORITES_STORAGE_KEY = "foundation-intelligence-favorites-v1";
const NEWS_RUN_STORAGE_KEY = "foundation-intelligence-news-runs-v1";
const EMPTY_FAVORITES: FavoritesState = { profiles: [], donors: [], donorRequests: [], researchViews: [], grantExplorers: [] };
const NEWS_PROGRESS_STEPS: Array<{ key: NewsProgressStep; label: string; detail: string }> = [
  { key: "discovering", label: "Find recent coverage", detail: "Search current Google News coverage for this organization." },
  { key: "reading", label: "Read source articles", detail: "Resolve publisher links and extract article evidence." },
  { key: "summarizing", label: "Create AI briefing", detail: "Generate a cited briefing from the collected evidence." },
];

function loadFavorites(): FavoritesState {
  try {
    const stored = window.localStorage.getItem(FAVORITES_STORAGE_KEY);
    if (!stored) return EMPTY_FAVORITES;
    const parsed = JSON.parse(stored) as Partial<FavoritesState>;
    return {
      profiles: Array.isArray(parsed.profiles) ? parsed.profiles : [],
      donors: Array.isArray(parsed.donors) ? parsed.donors : [],
      donorRequests: Array.isArray(parsed.donorRequests) ? parsed.donorRequests : [],
      researchViews: Array.isArray(parsed.researchViews) ? parsed.researchViews : [],
      grantExplorers: Array.isArray(parsed.grantExplorers) ? parsed.grantExplorers : [],
    };
  } catch {
    return EMPTY_FAVORITES;
  }
}

function loadNewsRuns(): SavedNewsRun[] {
  try {
    const stored = window.localStorage.getItem(NEWS_RUN_STORAGE_KEY);
    if (!stored) return [];
    const parsed = JSON.parse(stored);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((run): run is SavedNewsRun => (
      run
      && typeof run.organizationKey === "string"
      && typeof run.foundation === "string"
      && typeof run.summary === "string"
      && Array.isArray(run.sources)
      && typeof run.savedAt === "string"
    ));
  } catch {
    return [];
  }
}

function newsOrganizationKey(charity: Charity): string {
  return `organization:${charity.registered_charity_number}:${charity.source_record_id || ""}`;
}

function formatNewsDate(value: string | null | undefined): string {
  if (!value) return "Date unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(date);
}

function newsSummaryPlainText(summary: string): string {
  return summary
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[`*_>#]/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function pdfSafeText(value: string): string {
  const replacements: Record<string, string> = {
    "–": "-", "—": "-", "…": "...", "“": "\"", "”": "\"", "‘": "'", "’": "'", "•": "-",
  };
  return value
    .replace(/[–—…“”‘’•]/g, character => replacements[character] || character)
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^\x20-\x7E]/g, "?")
    .replace(/\\/g, "\\\\")
    .replace(/\(/g, "\\(")
    .replace(/\)/g, "\\)");
}

function wrapPdfText(value: string, limit = 88): string[] {
  const lines: string[] = [];
  for (const paragraph of value.split("\n")) {
    const words = paragraph.trim().split(/\s+/).filter(Boolean);
    if (!words.length) {
      lines.push("");
      continue;
    }
    let line = "";
    for (const word of words) {
      if (!line) {
        line = word;
      } else if (line.length + word.length + 1 <= limit) {
        line += ` ${word}`;
      } else {
        lines.push(line);
        line = word;
      }
    }
    if (line) lines.push(line);
  }
  return lines;
}

function createSimplePdf(lines: string[]): Blob {
  const linesPerPage = 48;
  const pages = Array.from(
    { length: Math.max(1, Math.ceil(lines.length / linesPerPage)) },
    (_, index) => lines.slice(index * linesPerPage, (index + 1) * linesPerPage),
  );
  const fontObjectId = 3 + pages.length * 2;
  const objects: string[] = Array.from({ length: fontObjectId + 1 }, () => "");
  objects[1] = "<< /Type /Catalog /Pages 2 0 R >>";
  objects[2] = `<< /Type /Pages /Kids [${pages.map((_, index) => `${3 + index * 2} 0 R`).join(" ")}] /Count ${pages.length} >>`;
  pages.forEach((pageLines, index) => {
    const pageObjectId = 3 + index * 2;
    const contentObjectId = pageObjectId + 1;
    const stream = [
      "BT",
      "/F1 10 Tf",
      "14 TL",
      "50 792 Td",
      ...pageLines.flatMap(line => [`(${pdfSafeText(line)}) Tj`, "T*"]),
      "ET",
    ].join("\n");
    objects[pageObjectId] = `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 ${fontObjectId} 0 R >> >> /Contents ${contentObjectId} 0 R >>`;
    objects[contentObjectId] = `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`;
  });
  objects[fontObjectId] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>";

  let pdf = "%PDF-1.4\n";
  const offsets = [0];
  for (let index = 1; index <= fontObjectId; index += 1) {
    offsets[index] = pdf.length;
    pdf += `${index} 0 obj\n${objects[index]}\nendobj\n`;
  }
  const xrefOffset = pdf.length;
  pdf += `xref\n0 ${fontObjectId + 1}\n0000000000 65535 f \n`;
  for (let index = 1; index <= fontObjectId; index += 1) {
    pdf += `${String(offsets[index]).padStart(10, "0")} 00000 n \n`;
  }
  pdf += `trailer\n<< /Size ${fontObjectId + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF`;
  return new Blob([pdf], { type: "application/pdf" });
}

function downloadNewsBriefingPdf(briefing: NewsSummaryPayload): void {
  const lines = [
    "Foundation Intelligence Platform",
    "AI News Briefing",
    "",
    `Organization: ${briefing.foundation}`,
    `Generated: ${formatNewsDate(briefing.generated_at)}`,
    `Coverage: last ${briefing.searched_weeks} weeks`,
    "",
    "SUMMARY",
    ...wrapPdfText(newsSummaryPlainText(briefing.summary)),
    "",
    "CITED SOURCES",
    ...briefing.sources.flatMap((source, index) => [
      `${index + 1}. ${source.title}`,
      `   ${source.source} - ${formatNewsDate(source.published)}`,
      `   ${source.link}`,
      ...(source.note ? [`   Note: ${source.note}`] : []),
      "",
    ]),
    "This briefing is evidence support, not a statement from the organization.",
  ];
  const url = URL.createObjectURL(createSimplePdf(lines));
  const anchor = document.createElement("a");
  const date = new Date(briefing.generated_at);
  const suffix = Number.isNaN(date.getTime()) ? "briefing" : date.toISOString().slice(0, 10);
  const name = briefing.foundation
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/gi, "-")
    .replace(/(^-|-$)/g, "")
    .toLowerCase() || "organization";
  anchor.href = url;
  anchor.download = `${name}-news-briefing-${suffix}.pdf`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

interface KPIStats {
  total_charities: number;
  active_charities: number;
  removed_charities: number;
  average_income: number;
  average_expenditure: number;
  total_grants?: number | null;
  data_mode?: string;
  source?: string[];
}

interface GrantTrendItem {
  month: string;
  grant_count: number | null;
  source_record_count: number;
  total_amount: number | null;
  coverage_status: "observed" | "partial" | "unknown";
}

interface GrantTrendsResponse {
  status: string;
  currency: string | null;
  available_currencies: string[];
  date_basis: string;
  period: { from: string; to: string; months: number; anchor: string } | null;
  items: GrantTrendItem[];
  excluded: Record<string, number>;
  zero_amount_count: number;
  latest_award_date: string | null;
  last_refreshed_at: string | null;
  source: string[];
  data_mode: string;
  scope: { coverage_note: string };
}

interface ProgrammeAllocationItem {
  programme_area: string;
  distinct_grant_count: number;
  weighted_grant_count: number;
  allocated_amount: number;
  source_classified_grant_count: number;
  inferred_classified_grant_count: number;
}

interface GrantThemesResponse {
  status: string;
  currency: string | null;
  available_currencies: string[];
  allocation_method: string;
  classification_precedence: string[];
  inference_confidence_threshold: number;
  items: ProgrammeAllocationItem[];
  classification_coverage: {
    qualifying_grant_count: number;
    classified_grant_count: number;
    unclassified_grant_count: number;
    classified_percentage: number;
    source_classified_grant_count: number;
    inferred_classified_grant_count: number;
    source_percentage: number;
    inferred_percentage: number;
    multiple_programme_area_grant_count: number;
    invalid_source_label_count: number;
    low_confidence_inference_count: number;
  };
  qualifying_amount: number;
  allocated_amount: number;
  excluded: Record<string, number>;
  zero_amount_count: number;
  last_refreshed_at: string | null;
  source: string[];
  data_mode: string;
  scope: { coverage_note: string };
}

interface GrantDetail {
  grant_id: string;
  funding_charity_id: number | null;
  funding_name: string | null;
  recipient_name: string;
  recipient_charity_id: number | null;
  amount: number | null;
  amount_eur: number | null;
  exchange_rate: number | null;
  exchange_rate_date: string | null;
  exchange_rate_source: string | null;
  conversion_status: string | null;
  currency: string;
  description: string;
  date: string;
  recipient_region: string;
  tags: string[];
}

interface SankeyNode {
  id?: string;
  name: string;
  role?: "donor" | "recipient";
  depth?: number;
}

interface SankeyLink {
  source: number;
  target: number;
  value: number;
  grantCount: number;
}

interface SankeyData {
  status: string;
  nodes: SankeyNode[];
  links: SankeyLink[];
  currency: string | null;
  excludedCount: number;
}

interface ScoreComponent {
  score: number | null;
  weight: number;
  weighted_score: number | null;
  confidence: number;
  available: boolean;
  evidence: Record<string, unknown>[];
  missing_reason: string | null;
}

interface ScoreResponse {
  score: number | null;
  score_target: string;
  score_version: string;
  configuration_status: string;
  confidence: number;
  data_completeness: number;
  components: Record<string, ScoreComponent>;
  missing_inputs: string[];
  review_required: boolean;
  assumptions: string[];
  not_a_prediction: boolean;
}

type ProfileLoadingKey = "detail" | "grants" | "relationships" | "score" | "source_record";

type ProfileSectionStatus = "idle" | "loading" | "ready" | "empty" | "partial" | "error";

type ProfileSectionState = {
  status: ProfileSectionStatus;
  error: string | null;
};

type ProfileLoadingState = Record<ProfileLoadingKey, ProfileSectionState>;

const profileSectionState = (status: ProfileSectionStatus, error: string | null = null): ProfileSectionState => ({
  status,
  error,
});

const IDLE_PROFILE_LOADING: ProfileLoadingState = {
  detail: profileSectionState("idle"),
  grants: profileSectionState("idle"),
  relationships: profileSectionState("idle"),
  score: profileSectionState("idle"),
  source_record: profileSectionState("idle"),
};

const INITIAL_PROFILE_LOADING: ProfileLoadingState = {
  detail: profileSectionState("loading"),
  grants: profileSectionState("loading"),
  relationships: profileSectionState("loading"),
  score: profileSectionState("loading"),
  source_record: profileSectionState("idle"),
};

type GlobalApiErrorKey = "health" | "statistics" | "directory" | "source_reset";
type GlobalApiErrors = Partial<Record<GlobalApiErrorKey, string>>;

function abortableDelay(
  durationMs: number,
  signal: AbortSignal,
  timerRef?: { current: number | null },
): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      const error = new Error("Aborted");
      error.name = "AbortError";
      reject(error);
      return;
    }

    let timer = 0;
    const cleanup = () => {
      signal.removeEventListener("abort", handleAbort);
      if (timerRef?.current === timer) timerRef.current = null;
    };
    const handleAbort = () => {
      window.clearTimeout(timer);
      cleanup();
      const error = new Error("Aborted");
      error.name = "AbortError";
      reject(error);
    };
    timer = window.setTimeout(() => {
      cleanup();
      resolve();
    }, durationMs);
    if (timerRef) timerRef.current = timer;
    signal.addEventListener("abort", handleAbort, { once: true });
  });
}

interface PipelineStatus {
  status: string;
  started_at: string | null;
  finished_at: string | null;
  last_run_source: string | null;
  error: string | null;
}

// Fallback Mock Data for demo when BFF is offline
const MOCK_STATS: KPIStats = {
  total_charities: 15,
  active_charities: 15,
  removed_charities: 0,
  average_income: 341000000,
  average_expenditure: 329000000
};

const MOCK_CHARITIES: Charity[] = [
  { registered_charity_number: 202918, suffix: 0, link: "#", charity_name: "Oxfam GB", reg_status: "R", reporting_status: "Registered", removal_reason: null, latest_income: 339366903, latest_expenditure: 362636196 },
  { registered_charity_number: 220949, suffix: 0, link: "#", charity_name: "The British Red Cross Society", reg_status: "R", reporting_status: "Registered", removal_reason: null, latest_income: 271500000, latest_expenditure: 259300000 },
  { registered_charity_number: 213890, suffix: 0, link: "#", charity_name: "Save the Children Fund", reg_status: "R", reporting_status: "Registered", removal_reason: null, latest_income: 294000000, latest_expenditure: 288000000 },
  { registered_charity_number: 326568, suffix: 0, link: "#", charity_name: "Comic Relief", reg_status: "R", reporting_status: "Registered", removal_reason: null, latest_income: 81200000, latest_expenditure: 74600000 },
  { registered_charity_number: 1089464, suffix: 0, link: "#", charity_name: "Cancer Research UK", reg_status: "R", reporting_status: "Registered", removal_reason: null, latest_income: 647000000, latest_expenditure: 618000000 },
  { registered_charity_number: 205846, suffix: 0, link: "#", charity_name: "The National Trust", reg_status: "R", reporting_status: "Registered", removal_reason: null, latest_income: 766000000, latest_expenditure: 824000000 },
  { registered_charity_number: 209603, suffix: 0, link: "#", charity_name: "Royal National Lifeboat Institution (RNLI)", reg_status: "R", reporting_status: "Registered", removal_reason: null, latest_income: 231000000, latest_expenditure: 219000000 },
  { registered_charity_number: 261017, suffix: 0, link: "#", charity_name: "Macmillan Cancer Support", reg_status: "R", reporting_status: "Registered", removal_reason: null, latest_income: 242000000, latest_expenditure: 235000000 },
  { registered_charity_number: 216250, suffix: 0, link: "#", charity_name: "Barnardo's", reg_status: "R", reporting_status: "Registered", removal_reason: null, latest_income: 298000000, latest_expenditure: 289000000 },
  { registered_charity_number: 1128267, suffix: 0, link: "#", charity_name: "Age UK", reg_status: "R", reporting_status: "Registered", removal_reason: null, latest_income: 153000000, latest_expenditure: 147000000 },
  { registered_charity_number: 209617, suffix: 0, link: "#", charity_name: "Guide Dogs for the Blind Association", reg_status: "R", reporting_status: "Registered", removal_reason: null, latest_income: 112000000, latest_expenditure: 108000000 },
  { registered_charity_number: 219099, suffix: 0, link: "#", charity_name: "RSPCA", reg_status: "R", reporting_status: "Registered", removal_reason: null, latest_income: 149000000, latest_expenditure: 142000000 },
  { registered_charity_number: 216401, suffix: 0, link: "#", charity_name: "NSPCC", reg_status: "R", reporting_status: "Registered", removal_reason: null, latest_income: 125000000, latest_expenditure: 119000000 },
  { registered_charity_number: 225971, suffix: 0, link: "#", charity_name: "British Heart Foundation", reg_status: "R", reporting_status: "Registered", removal_reason: null, latest_income: 158000000, latest_expenditure: 149000000 },
  { registered_charity_number: 1160558, suffix: 0, link: "#", charity_name: "Great Ormond Street Hospital Charity", reg_status: "R", reporting_status: "Registered", removal_reason: null, latest_income: 98000000, latest_expenditure: 91000000 }
];

const EMPTY_MAP: GrantMapResponse = {
  status: "data_unavailable",
  geographic_dimension: "beneficiary_location",
  items: [],
  known_geography_count: 0,
  unknown_geography_count: 0,
  coverage_percentage: 0,
  currencies: [],
  selected_currency: null,
  funding_status: "unavailable",
  funding_mode_available: false,
  grant_country_association_count: 0,
  multi_country_grant_count: 0,
  funding_excluded_multi_country_count: 0,
  funding_excluded_multi_country_amount: 0,
  funding_excluded_currency_count: 0,
  funding_excluded_invalid_amount_count: 0,
  connections: [],
  connection_grant_count: 0,
  connection_excluded_no_headquarters_count: 0,
  connection_same_country_count: 0,
  minimum_coverage_threshold: 0.30,
  metadata: {
    data_mode: "unavailable",
    source: [],
    record_count: 0,
    limitations: ["The transaction geography endpoint has not been loaded."],
  },
};

const renderMarkdown = (text: string) => {
  if (!text) return null;

  const lines = text.split("\n");
  let inList = false;
  const elements: React.ReactNode[] = [];
  let listItems: React.ReactNode[] = [];

  const parseInlineStyles = (lineText: string) => {
    const segments = lineText.split("**");
    return segments.map((seg, idx) => {
      if (idx % 2 === 1) {
        return <strong key={idx} style={{ color: "var(--text-primary)", fontWeight: "700" }}>{seg}</strong>;
      }
      return seg;
    });
  };

  lines.forEach((line, idx) => {
    const trimmed = line.trim();

    if (trimmed.startsWith("###")) {
      if (inList) {
        elements.push(<ul key={`list-${idx}`} style={{ margin: "8px 0 16px 20px", display: "flex", flexDirection: "column", gap: "6px", listStyleType: "disc" }}>{listItems}</ul>);
        listItems = [];
        inList = false;
      }
      elements.push(
        <h4 key={idx} style={{ fontSize: "14px", fontWeight: "700", marginTop: "16px", marginBottom: "8px", color: "var(--nl-unicorn)" }}>
          {parseInlineStyles(trimmed.slice(3).trim())}
        </h4>
      );
    } else if (trimmed.startsWith("##")) {
      if (inList) {
        elements.push(<ul key={`list-${idx}`} style={{ margin: "8px 0 16px 20px", display: "flex", flexDirection: "column", gap: "6px", listStyleType: "disc" }}>{listItems}</ul>);
        listItems = [];
        inList = false;
      }
      elements.push(
        <h3 key={idx} style={{ fontSize: "16px", fontWeight: "700", marginTop: "20px", marginBottom: "10px", color: "var(--nl-unicorn)" }}>
          {parseInlineStyles(trimmed.slice(2).trim())}
        </h3>
      );
    } else if (trimmed.startsWith("#")) {
      if (inList) {
        elements.push(<ul key={`list-${idx}`} style={{ margin: "8px 0 16px 20px", display: "flex", flexDirection: "column", gap: "6px", listStyleType: "disc" }}>{listItems}</ul>);
        listItems = [];
        inList = false;
      }
      elements.push(
        <h2 key={idx} style={{ fontSize: "18px", fontWeight: "700", marginTop: "24px", marginBottom: "12px", color: "var(--nl-unicorn)" }}>
          {parseInlineStyles(trimmed.replace(/^#\s*/, "").trim())}
        </h2>
      );
    } else if (trimmed.startsWith("* ") || trimmed.startsWith("- ")) {
      inList = true;
      listItems.push(
        <li key={idx} style={{ fontSize: "13.5px", lineHeight: "1.5", color: "var(--text-secondary)" }}>
          {parseInlineStyles(trimmed.slice(2).trim())}
        </li>
      );
    } else if (!trimmed) {
      if (inList) {
        elements.push(<ul key={`list-${idx}`} style={{ margin: "8px 0 16px 20px", display: "flex", flexDirection: "column", gap: "6px", listStyleType: "disc" }}>{listItems}</ul>);
        listItems = [];
        inList = false;
      }
    } else {
      if (inList) {
        elements.push(<ul key={`list-${idx}`} style={{ margin: "8px 0 16px 20px", display: "flex", flexDirection: "column", gap: "6px", listStyleType: "disc" }}>{listItems}</ul>);
        listItems = [];
        inList = false;
      }
      elements.push(
        <p key={idx} style={{ margin: "0 0 12px 0", fontSize: "13.5px", lineHeight: "1.6", color: "var(--text-secondary)" }}>
          {parseInlineStyles(line)}
        </p>
      );
    }
  });

  if (inList && listItems.length > 0) {
    elements.push(<ul key="list-trailing" style={{ margin: "8px 0 16px 20px", display: "flex", flexDirection: "column", gap: "6px", listStyleType: "disc" }}>{listItems}</ul>);
  }

  return <div className="markdown-content">{elements}</div>;
};

const ANNUAL_GIVING_STEPS = [0, 100000, 500000, 1000000, 5000000, 10000000, 50000000, 100000000, 500000000];
const ANNUAL_GIVING_LABELS = ["£0", "£100k", "£500k", "£1M", "£5M", "£10M", "£50M", "£100M", "£500M"];

const AVG_GRANT_SIZE_STEPS = [0, 1000, 5000, 10000, 50000, 100000, 250000, 500000, 1000000];
const AVG_GRANT_SIZE_LABELS = ["€0", "€1k", "€5k", "€10k", "€50k", "€100k", "€250k", "€500k", "€1M"];

const optionalFilterAmount = (value: string): number | null => {
  if (!value.trim()) return null;
  const amount = Number(value);
  return Number.isFinite(amount) && amount >= 0 ? amount : null;
};

const SECTORS = [
  { value: "Socio-economic Development, Poverty", label: "Poverty & Economic Development" },
  { value: "Environment/Climate", label: "Climate & Environment" },
  { value: "Youth/Children Development", label: "Children & Youth" },
  { value: "Food, Agriculture & Nutrition", label: "Food & Nutrition" },
  { value: "tech-enablement", label: "Tech Enablement" },
  { value: "Sciences & Research", label: "Sciences & Research" },
  { value: "Health", label: "Health" },
  { value: "Arts & Culture", label: "Arts & Culture" },
  { value: "Humanitarian & Disaster Relief", label: "Humanitarian & Disaster" },
  { value: "Human/Civil Rights", label: "Human & Civil Rights" },
  { value: "Diversity & Inclusion", label: "Diversity & Inclusion" },
  { value: "Civil society, Voluntarism & Non-Profit Sector", label: "Civil Society" },
  { value: "Citizenship, Social Justice & Public Affairs", label: "Citizenship & Public Affairs" },
  { value: "Peace & Conflict Resolution", label: "Peace & Conflict Resolution" }
];

const HEADQUARTERS_LOCATIONS = ["United Kingdom", "Germany", "Austria", "Switzerland", "France", "Netherlands", "Denmark", "Norway"];
const BENEFICIARY_GEOGRAPHIES = ["United Kingdom", "Ghana", "Kenya", "Tanzania", "Uganda", "South Africa", "India", "Worldwide", "Europe (DACH)"];

const EMPTY_GRANT_MAP_FILTERS: GrantMapFilters = {
  search: "",
  tags: [],
  foundationRegions: [],
  fundingRegions: [],
  minAnnualGiving: 0,
  minAvgGrantSize: 0,
};

export default function App() {
  const hasFunderRoute = () => Boolean(new URLSearchParams(window.location.search).get("funder_country"));
  const initialView = () => new URLSearchParams(window.location.search).get("view");
  const [activeTab, setActiveTab] = useState<"overview" | "directory" | "favorites" | "admin">(() => initialView() === "pipeline" ? "admin" : initialView() === "favorites" ? "favorites" : (hasFunderRoute() || ["donors", "research", "registry"].includes(initialView() || "")) ? "directory" : "overview");
  const [directoryMode, setDirectoryMode] = useState<"donors" | "profiles" | "registry">(() => initialView() === "research" ? "profiles" : initialView() === "registry" ? "registry" : "donors");
  const [overviewFilterCount, setOverviewFilterCount] = useState(0);
  const [overviewFiltersOpen, setOverviewFiltersOpen] = useState(false);
  const [donorHeaderState, setDonorHeaderState] = useState<HeaderContextState>({
    filterCount: 0,
    resetDisabled: true,
    filtersExpanded: false,
  });
  const [registryHeaderState, setRegistryHeaderState] = useState<HeaderContextState>({
    filterCount: 0,
    resetDisabled: true,
    filtersExpanded: false,
  });
  const [profileFiltersOpen, setProfileFiltersOpen] = useState(false);
  const [favorites, setFavorites] = useState<FavoritesState>(loadFavorites);
  const [favoriteDonorWorkspace, setFavoriteDonorWorkspace] = useState<FavoriteDonorWorkspace | null>(null);
  const [favoriteGrantExplorer, setFavoriteGrantExplorer] = useState<FavoriteGrantExplorerPayload | null>(null);
  const favoriteReturnUrlRef = useRef<string | null>(null);
  const favoriteReturnScrollRef = useRef(0);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    const saved = window.localStorage.getItem("sidebar-collapsed");
    return saved === null ? window.innerWidth < 1280 : saved === "true";
  });
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);

  // Data states
  const [stats, setStats] = useState<KPIStats>(MOCK_STATS);
  const [dataSourceSelections, setDataSourceSelections] = useState<Record<string, boolean>>(() => {
    const params = new URLSearchParams(window.location.search);
    if (!params.has("grant_sources")) return {};
    const selected = new Set((params.get("grant_sources") || "").split(",").filter(Boolean));
    return Object.fromEntries(DEFAULT_DATA_SOURCES.map(source => [source, selected.has(source)]));
  });
  const [charities, setCharities] = useState<Charity[]>(MOCK_CHARITIES);
  const [profileSort, setProfileSort] = useState<"score_desc" | "income_desc" | "name_asc">("score_desc");
  const [profileOffset, setProfileOffset] = useState(0);
  const [profilesHaveMore, setProfilesHaveMore] = useState(false);
  const [loadingMoreProfiles, setLoadingMoreProfiles] = useState(false);
  const [directoryInitialLoaded, setDirectoryInitialLoaded] = useState(false);
  const directoryRequestRef = useRef<AbortController | null>(null);
  const directoryRequestSequenceRef = useRef(0);
  const [mapData, setMapData] = useState<GrantMapResponse>(EMPTY_MAP);
  const [mapLoading, setMapLoading] = useState(false);
  const [mapError, setMapError] = useState<string | null>(null);
  const mapRequestRef = useRef<AbortController | null>(null);
  const [mapFilters] = useState<GrantMapFilters>(EMPTY_GRANT_MAP_FILTERS);
  const [grantTrends, setGrantTrends] = useState<GrantTrendsResponse | null>(null);
  const [grantThemes, setGrantThemes] = useState<GrantThemesResponse | null>(null);
  const [grantAnalyticsLoading, setGrantAnalyticsLoading] = useState(false);
  const [grantAnalyticsError, setGrantAnalyticsError] = useState<string | null>(null);
  const [grantAnalyticsCurrency, setGrantAnalyticsCurrency] = useState("");
  const grantAnalyticsRequestRef = useRef<AbortController | null>(null);
  const [selectedCharity, setSelectedCharity] = useState<Charity | null>(null);
  const [selectedCharityDetail, setSelectedCharityDetail] = useState<any>(null);
  const [selectedSourceFunderProfileKey, setSelectedSourceFunderProfileKey] = useState<string | null>(null);
  const [selectedNewsAliases, setSelectedNewsAliases] = useState<string[]>([]);
  const [charityGrants, setCharityGrants] = useState<GrantDetail[]>([]);
  const [grantStatus, setGrantStatus] = useState("data_unavailable");
  const [sankeyData, setSankeyData] = useState<SankeyData | null>(null);
  const [scoreData, setScoreData] = useState<ScoreResponse | null>(null);
  const [profileLoading, setProfileLoading] = useState<ProfileLoadingState>(IDLE_PROFILE_LOADING);
  const profileRequestSequenceRef = useRef(0);
  const profileAbortControllerRef = useRef<AbortController | null>(null);
  const profileModalRef = useRef<HTMLDivElement | null>(null);
  const profilePreviousFocusRef = useRef<HTMLElement | null>(null);
  const sourceHydrationTimerRef = useRef<number | null>(null);
  const selectedSourceFunderProfileKeyRef = useRef<string | null>(null);
  const sourceFunderProfilePayloadsRef = useRef(new Map<string, Record<string, any>>());
  const [activeSourceFunder, setActiveSourceFunder] = useState<ActiveSourceFunder | null>(null);
  const [sourceFunderResetPending, setSourceFunderResetPending] = useState(false);

  // News summarizer states
  const [newsLoading, setNewsLoading] = useState(false);
  const [newsSummary, setNewsSummary] = useState<NewsSummaryPayload | null>(null);
  const [newsError, setNewsError] = useState<string | null>(null);
  const [newsProgressStep, setNewsProgressStep] = useState<NewsProgressStep | null>(null);
  const [newsRunMode, setNewsRunMode] = useState<"live" | "illustrative" | null>(null);
  const [newsRuns, setNewsRuns] = useState<SavedNewsRun[]>(loadNewsRuns);
  const newsAbortControllerRef = useRef<AbortController | null>(null);
  const newsRequestSequenceRef = useRef(0);
  const selectedNewsOrganizationKeyRef = useRef<string | null>(null);

  // Filter states
  const [searchTerm, setSearchTerm] = useState("");
  const [organizationSuggestions, setOrganizationSuggestions] = useState<Charity[]>([]);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [selectedFoundationRegions, setSelectedFoundationRegions] = useState<string[]>([]);
  const [selectedRecipientRegions, setSelectedRecipientRegions] = useState<string[]>([]);
  const [beneficiaryLocationOptions, setBeneficiaryLocationOptions] = useState<string[]>(BENEFICIARY_GEOGRAPHIES);
  const [directoryHandoff, setDirectoryHandoff] = useState({ version: 0, query: "", beneficiaryGeography: "" });
  const [annualGivingIndex, setAnnualGivingIndex] = useState<number>(0);
  const [maxAnnualGivingInput, setMaxAnnualGivingInput] = useState("");
  const [avgGrantSizeIndex, setAvgGrantSizeIndex] = useState<number>(0);
  const [maxAvgGrantSizeInput, setMaxAvgGrantSizeInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [isBffOnline, setIsBffOnline] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [apiErrors, setApiErrors] = useState<GlobalApiErrors>({});
  const [authError, setAuthError] = useState<string | null>(null);

  const setApiError = (key: GlobalApiErrorKey, message: string | null) => {
    setApiErrors(current => {
      if (!message) {
        if (!(key in current)) return current;
        const next = { ...current };
        delete next[key];
        return next;
      }
      if (current[key] === message) return current;
      return { ...current, [key]: message };
    });
  };

  const isCurrentProfileRequest = (requestId: number) =>
    profileRequestSequenceRef.current === requestId;

  const setProfileSection = (
    requestId: number,
    key: ProfileLoadingKey,
    status: ProfileSectionStatus,
    error: string | null = null,
  ) => {
    if (!isCurrentProfileRequest(requestId)) return;
    setProfileLoading(current => ({ ...current, [key]: profileSectionState(status, error) }));
  };

  const finishProfileSection = (requestId: number, key: ProfileLoadingKey) => {
    if (!isCurrentProfileRequest(requestId)) return;
    setProfileLoading(current => current[key].status === "loading"
      ? { ...current, [key]: profileSectionState("ready") }
      : current);
  };

  useEffect(() => {
    if (activeTab !== "directory" || directoryMode !== "profiles") setProfileFiltersOpen(false);
  }, [activeTab, directoryMode]);

  useEffect(() => {
    const syncRouteMode = () => {
      const view = initialView();
      if (view === "pipeline") {
        setActiveTab("admin");
      } else if (view === "favorites") {
        setActiveTab("favorites");
      } else if (view === "research") {
        setDirectoryMode("profiles");
        setActiveTab("directory");
      } else if (view === "registry") {
        setDirectoryMode("registry");
        setActiveTab("directory");
      } else if (view === "donors" || hasFunderRoute()) {
        setDirectoryMode("donors");
        setActiveTab("directory");
      } else {
        setActiveTab("overview");
      }
    };
    window.addEventListener("popstate", syncRouteMode);
    return () => window.removeEventListener("popstate", syncRouteMode);
  }, []);

  // The stats endpoint returns a fresh array on every response. Keep the
  // source list referentially stable while its values are unchanged so it
  // cannot retrigger directory and overview requests indefinitely.
  const dataSourceNamesKey = (stats.source?.length ? stats.source : DEFAULT_DATA_SOURCES).join("\u001f");
  const dataSourceNames = useMemo(
    () => dataSourceNamesKey.split("\u001f").filter(Boolean),
    [dataSourceNamesKey],
  );
  const selectedDataSources = useMemo(
    () => dataSourceNames.filter(source => dataSourceSelections[source] !== false),
    [dataSourceNames, dataSourceSelections],
  );
  const toggleDataSource = (source: string) => {
    setDataSourceSelections(current => ({
      ...current,
      [source]: current[source] === false,
    }));
  };

  useEffect(() => {
    const currentScope = grantScopeFromUrl(window.location.search, selectedDataSources);
    const params = applyGrantScopeToParams(
      new URLSearchParams(window.location.search),
      { ...currentScope, sources: selectedDataSources },
      { persistEmptySources: true },
    );
    const suffix = params.toString();
    window.history.replaceState(
      window.history.state,
      "",
      `${window.location.pathname}${suffix ? `?${suffix}` : ""}`,
    );
  }, [selectedDataSources]);

  useEffect(() => {
    const syncSourcesFromRoute = () => {
      const params = new URLSearchParams(window.location.search);
      if (!params.has("grant_sources")) return;
      const selected = new Set((params.get("grant_sources") || "").split(",").filter(Boolean));
      setDataSourceSelections(Object.fromEntries(dataSourceNames.map(source => [source, selected.has(source)])));
    };
    window.addEventListener("popstate", syncSourcesFromRoute);
    return () => window.removeEventListener("popstate", syncSourcesFromRoute);
  }, [dataSourceNames]);
  const selectedCharityId = selectedCharity?.registered_charity_number ?? null;
  const selectedNewsOrganizationKey = selectedCharity ? newsOrganizationKey(selectedCharity) : null;
  selectedNewsOrganizationKeyRef.current = selectedNewsOrganizationKey;
  const profileIsLoading = selectedCharity !== null
    && Object.values(profileLoading).some(section => section.status === "loading");
  const profileSectionErrors = (Object.entries(profileLoading) as Array<[ProfileLoadingKey, ProfileSectionState]>)
    .filter(([, section]) => section.status === "error" && section.error);
  const apiErrorMessages = Object.values(apiErrors).filter((message): message is string => Boolean(message));
  const visibleApiErrors = [authError, ...apiErrorMessages].filter((message): message is string => Boolean(message));
  const savedNewsRun = useMemo(() => {
    if (!selectedCharity) return null;
    return newsRuns.find(run => run.organizationKey === newsOrganizationKey(selectedCharity)) || null;
  }, [newsRuns, selectedCharity]);
  const savedNewsRunIsOpen = Boolean(
    savedNewsRun
    && newsSummary
    && newsSummary.generated_at === savedNewsRun.generated_at,
  );
  const visibleNewsSources = useMemo(() => Array.from(new Map(
    (newsSummary?.sources || []).map(source => [`${source.link}\u001f${source.title}`, source]),
  ).values()), [newsSummary?.sources]);
  const financialHistoryData = useMemo(() => {
    const history = selectedCharityDetail?.financial_history;
    if (!Array.isArray(history)) return [];
    return [...history]
      .sort((left: any, right: any) =>
        new Date(left.financial_period_end_date || "").getTime()
        - new Date(right.financial_period_end_date || "").getTime(),
      )
      .map((item: any) => ({
        year: item.financial_period_end_date
          ? new Date(item.financial_period_end_date).getFullYear().toString()
          : "N/A",
        Income: typeof item.income === "number" && Number.isFinite(item.income) ? item.income : null,
        Expenditure: typeof item.expenditure === "number" && Number.isFinite(item.expenditure) ? item.expenditure : null,
      }));
  }, [selectedCharityDetail?.financial_history]);
  const selectedCharityFlowRows = useMemo(() => {
    if (!sankeyData || selectedCharityId === null) return [];
    const selectedNodeId = `organization:${selectedCharityId}`;
    return sankeyData.links
      .map(link => {
        const source = sankeyData.nodes[link.source];
        const target = sankeyData.nodes[link.target];
        if (!source || !target) return null;
        if (source.id === selectedNodeId) {
          return { counterparty: target.name, direction: "Awarded to", amount: link.value, grantCount: link.grantCount };
        }
        if (target.id === selectedNodeId) {
          return { counterparty: source.name, direction: "Received from", amount: link.value, grantCount: link.grantCount };
        }
        return null;
      })
      .filter((row): row is { counterparty: string; direction: "Awarded to" | "Received from"; amount: number; grantCount: number } => row !== null)
      .sort((left, right) => right.amount - left.amount);
  }, [sankeyData, selectedCharityId]);
  const selectedCharityFlowMaximum = selectedCharityFlowRows[0]?.amount || 0;
  const selectedCharityFlowTotal = selectedCharityFlowRows.reduce((total, row) => total + row.amount, 0);
  // Admin & pipeline states
  const [pipelineStatus, setPipelineStatus] = useState<PipelineStatus>({
    status: "idle",
    started_at: null,
    finished_at: null,
    last_run_source: null,
    error: null
  });
  const [logs, setLogs] = useState("System terminal ready.\n");
  const [isTriggering, setIsTriggering] = useState(false);
  const [pipelineLimit, setPipelineLimit] = useState<number>(20);
  const [pipelineFresh, setPipelineFresh] = useState<boolean>(false);
  const [enableImpressum, setEnableImpressum] = useState<boolean>(true);
  const [pipelineSearch, setPipelineSearch] = useState<string>("");
  const [pipelineIds, setPipelineIds] = useState<string>("");
  const logsEndRef = useRef<HTMLDivElement>(null);

  const runHealthCheck = useEffectEvent((signal: AbortSignal) => {
    void checkBffHealth(signal);
  });
  const refreshStatistics = useEffectEvent((signal?: AbortSignal) => {
    void fetchStats(undefined, signal);
  });
  const refreshDirectory = useEffectEvent(() => {
    void fetchCharities();
  });
  const loadProfileSections = useEffectEvent((
    id: number,
    sourceFunderKey: string | null,
    requestId: number,
    signal: AbortSignal,
  ) => {
    void fetchCharityDetail(id, requestId, signal);
    void fetchCharityGrants(id, requestId, signal);
    void fetchSankeyData(id, requestId, signal);
    void fetchScoreData(id, requestId, signal);
    if (sourceFunderKey) {
      void hydrateSourceFunderProfile(sourceFunderKey, id, requestId, signal);
    }
  });
  const refreshPipelineStatus = useEffectEvent((signal: AbortSignal) => {
    void fetchPipelineStatus(signal);
  });
  const refreshAfterPipelineSuccess = useEffectEvent((signal: AbortSignal) => {
    void fetchStats(undefined, signal);
    void fetchCharities();
    window.dispatchEvent(new Event("overview-refresh"));
  });

  // Fetch initial configuration on mount
  useEffect(() => {
    const controller = new AbortController();
    runHealthCheck(controller.signal);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!isBffOnline) return;
    const controller = new AbortController();
    refreshStatistics(controller.signal);
    return () => controller.abort();
  }, [isBffOnline]);

  useEffect(() => {
    if (activeTab !== "directory" || directoryMode !== "profiles") return;
    const delay = searchTerm.trim() || maxAnnualGivingInput || maxAvgGrantSizeInput ? 250 : 0;
    const debounce = window.setTimeout(() => {
      refreshDirectory();
    }, delay);
    return () => {
      window.clearTimeout(debounce);
      directoryRequestRef.current?.abort();
    };
  }, [
    activeTab,
    directoryMode,
    isBffOnline,
    searchTerm,
    selectedTags,
    selectedFoundationRegions,
    selectedRecipientRegions,
    annualGivingIndex,
    maxAnnualGivingInput,
    avgGrantSizeIndex,
    maxAvgGrantSizeInput,
    profileSort,
    selectedDataSources,
  ]);

  useEffect(() => {
    if (!isBffOnline) return;
    const controller = new AbortController();
    fetch(`${API_BASE}/api/charities/grants/beneficiary-geographies`, {
      credentials: "include",
      signal: controller.signal,
    })
      .then(async response => {
        if (!response.ok) throw new Error(`Beneficiary geography request failed (${response.status}).`);
        return response.json() as Promise<string[]>;
      })
      .then(options => {
        if (Array.isArray(options) && options.length) {
          setBeneficiaryLocationOptions(options);
        }
      })
      .catch(error => {
        if ((error as Error).name !== "AbortError") {
          // Keep the bundled fallback options; this panel is independently usable.
        }
      });
    return () => controller.abort();
  }, [isBffOnline]);

  useEffect(() => {
    const query = searchTerm.trim();
    if (query.length < 2) {
      setOrganizationSuggestions([]);
      setSuggestionsLoading(false);
      return;
    }

    const controller = new AbortController();
    const debounce = window.setTimeout(async () => {
      setSuggestionsLoading(true);
      try {
        if (!isBffOnline) {
          setOrganizationSuggestions(
            MOCK_CHARITIES
              .filter(charity => charity.charity_name.toLocaleLowerCase().includes(query.toLocaleLowerCase()))
              .slice(0, 6),
          );
          return;
        }
        const parameters = new URLSearchParams({ search: query, limit: "6" });
        parameters.set("sources", selectedDataSources.join(","));
        const response = await fetch(`${API_BASE}/api/charities?${parameters.toString()}`, {
          credentials: "include",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`Organization suggestion request failed (${response.status}).`);
        setOrganizationSuggestions(await response.json());
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
          setOrganizationSuggestions([]);
        }
      } finally {
        if (!controller.signal.aborted) setSuggestionsLoading(false);
      }
    }, 180);
    return () => {
      controller.abort();
      window.clearTimeout(debounce);
    };
  }, [isBffOnline, searchTerm, selectedDataSources]);

  useEffect(() => {
    newsAbortControllerRef.current?.abort();
    newsAbortControllerRef.current = null;
    newsRequestSequenceRef.current += 1;
    setNewsSummary(null);
    setNewsError(null);
    setNewsLoading(false);
    setNewsProgressStep(null);
    setNewsRunMode(null);
    return () => newsAbortControllerRef.current?.abort();
  }, [selectedNewsOrganizationKey]);

  useEffect(() => {
    // Every profile data source starts at once. A single cancellation scope and
    // sequence number prevent a slow response for (for example) Foothold from
    // overwriting the next profile or a reset view.
    profileAbortControllerRef.current?.abort();
    if (sourceHydrationTimerRef.current !== null) {
      window.clearTimeout(sourceHydrationTimerRef.current);
      sourceHydrationTimerRef.current = null;
    }
    const requestId = ++profileRequestSequenceRef.current;

    if (selectedCharityId === null) {
      profileAbortControllerRef.current = null;
      setSelectedCharityDetail(null);
      setCharityGrants([]);
      setGrantStatus("data_unavailable");
      setSankeyData(null);
      setScoreData(null);
      setProfileLoading(IDLE_PROFILE_LOADING);
      return;
    }

    const controller = new AbortController();
    profileAbortControllerRef.current = controller;
    setSelectedCharityDetail(null);
    setCharityGrants([]);
    setGrantStatus("data_unavailable");
    setSankeyData(null);
    setScoreData(null);
    setProfileLoading({
      ...INITIAL_PROFILE_LOADING,
      source_record: selectedSourceFunderProfileKey && isBffOnline
        ? profileSectionState("loading")
        : profileSectionState("idle"),
    });

    loadProfileSections(
      selectedCharityId,
      selectedSourceFunderProfileKey,
      requestId,
      controller.signal,
    );

    return () => {
      controller.abort();
      if (sourceHydrationTimerRef.current !== null) {
        window.clearTimeout(sourceHydrationTimerRef.current);
        sourceHydrationTimerRef.current = null;
      }
    };
  }, [selectedCharityId, selectedSourceFunderProfileKey, isBffOnline]);

  useEffect(() => {
    if (activeTab !== "admin") return;
    let controller: AbortController | null = null;
    const refresh = () => {
      controller?.abort();
      controller = new AbortController();
      refreshPipelineStatus(controller.signal);
    };
    refresh();
    const interval = window.setInterval(refresh, 3000);
    return () => {
      window.clearInterval(interval);
      controller?.abort();
    };
  }, [activeTab]);

  useEffect(() => {
    if (pipelineStatus.status !== "success") return;
    const controller = new AbortController();
    refreshAfterPipelineSuccess(controller.signal);
    return () => controller.abort();
  }, [pipelineStatus.status]);

  useEffect(() => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs]);

  useEffect(() => {
    window.localStorage.setItem("sidebar-collapsed", String(sidebarCollapsed));
  }, [sidebarCollapsed]);

  useEffect(() => {
    window.localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify(favorites));
  }, [favorites]);

  useEffect(() => {
    window.localStorage.setItem(NEWS_RUN_STORAGE_KEY, JSON.stringify(newsRuns));
  }, [newsRuns]);

  useEffect(() => {
    const receiveFilterCount = (event: Event) => setOverviewFilterCount(Number((event as CustomEvent<number>).detail) || 0);
    window.addEventListener("overview-filter-count", receiveFilterCount);
    return () => window.removeEventListener("overview-filter-count", receiveFilterCount);
  }, []);

  useEffect(() => {
    const receiveFilterDrawerState = (event: Event) => setOverviewFiltersOpen(Boolean((event as CustomEvent<boolean>).detail));
    window.addEventListener("overview-filter-drawer-state", receiveFilterDrawerState);
    return () => window.removeEventListener("overview-filter-drawer-state", receiveFilterDrawerState);
  }, []);

  useEffect(() => {
    const receiveRegistryHeaderState = (event: Event) => {
      const detail = (event as CustomEvent<HeaderContextState>).detail;
      if (detail) setRegistryHeaderState(detail);
    };
    window.addEventListener("registry-header-state", receiveRegistryHeaderState);
    return () => window.removeEventListener("registry-header-state", receiveRegistryHeaderState);
  }, []);

  useEffect(() => {
    const receiveActiveSourceFunder = (event: Event) => {
      const detail = (event as CustomEvent<ActiveSourceFunder | null>).detail;
      if (!detail?.sourceFunderKey) {
        setActiveSourceFunder(null);
        return;
      }
      setActiveSourceFunder({
        sourceFunderKey: detail.sourceFunderKey,
        displayName: detail.displayName || "Source funder",
      });
    };
    window.addEventListener("active-source-funder-change", receiveActiveSourceFunder);
    return () => window.removeEventListener("active-source-funder-change", receiveActiveSourceFunder);
  }, []);

  const checkBffHealth = async (signal?: AbortSignal) => {
    setInitialLoading(true);
    try {
      const resp = await fetch(`${API_BASE}/health`, { signal });
      if (signal?.aborted) return;
      if (resp.ok) {
        setAuthError(null);
        setIsBffOnline(true);
        setApiError("health", null);
        // Authentication is established by the deployment OIDC boundary.
        // The browser never contains or posts a shared application password.
        return;
      }
      setIsBffOnline(false);
      setApiError("health", "Backend unavailable. Values marked as illustrative are local prototype data.");
    } catch (error) {
      if ((error as Error).name === "AbortError") return;
      setIsBffOnline(false);
      setApiError("health", "Backend unavailable. Values marked as illustrative are local prototype data.");
    } finally {
      if (!signal?.aborted) setInitialLoading(false);
    }
  };

  const fetchStats = async (forceOnline?: boolean, signal?: AbortSignal) => {
    const isOnline = forceOnline !== undefined ? forceOnline : isBffOnline;
    if (!isOnline) return;
    try {
      const resp = await fetch(`${API_BASE}/api/charities/stats`, {
        credentials: "include",
        signal,
      });
      if (signal?.aborted) return;
      if (resp.ok) {
        const data = await resp.json();
        if (signal?.aborted) return;
        setStats(data);
        setApiError("statistics", null);
      } else {
        setApiError("statistics", `Statistics request failed (${resp.status}).`);
      }
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setApiError("statistics", "Statistics are temporarily unavailable.");
    }
  };

  const fetchCharities = async (forceOnline?: boolean, append = false) => {
    if (activeTab !== "directory" || directoryMode !== "profiles") return;
    const isOnline = forceOnline !== undefined ? forceOnline : isBffOnline;
    const pageSize = 50;
    const offset = append ? profileOffset : 0;
    directoryRequestRef.current?.abort();
    const controller = new AbortController();
    directoryRequestRef.current = controller;
    const requestSequence = ++directoryRequestSequenceRef.current;
    if (append) {
      setLoadingMoreProfiles(true);
    } else {
      setLoading(true);
      setProfilesHaveMore(false);
    }
    if (!isOnline) {
      // Offline local filtering of mocks
      let filtered = [...MOCK_CHARITIES];
      if (searchTerm) {
        filtered = filtered.filter(c => c.charity_name.toLowerCase().includes(searchTerm.toLowerCase()));
      }
      const minGiving = ANNUAL_GIVING_STEPS[annualGivingIndex];
      if (minGiving > 0) {
        filtered = filtered.filter(c => (c.latest_expenditure || 0) >= minGiving);
      }
      const maxGiving = optionalFilterAmount(maxAnnualGivingInput);
      if (maxGiving !== null) {
        filtered = filtered.filter(c => (c.latest_expenditure || 0) <= maxGiving);
      }
      const minAvgGrant = AVG_GRANT_SIZE_STEPS[avgGrantSizeIndex];
      if (minAvgGrant > 0) {
        filtered = filtered.filter(c => ((c.latest_expenditure || 0) / 10) >= minAvgGrant);
      }
      const maxAvgGrant = optionalFilterAmount(maxAvgGrantSizeInput);
      if (maxAvgGrant !== null) {
        filtered = filtered.filter(c => ((c.latest_expenditure || 0) / 10) <= maxAvgGrant);
      }
      if (profileSort === "score_desc") {
        filtered.sort((left, right) => (right.relevance_score || 0) - (left.relevance_score || 0) || left.charity_name.localeCompare(right.charity_name));
      } else if (profileSort === "income_desc") {
        filtered.sort((left, right) => (right.latest_income || 0) - (left.latest_income || 0) || left.charity_name.localeCompare(right.charity_name));
      } else {
        filtered.sort((left, right) => left.charity_name.localeCompare(right.charity_name));
      }
      const nextPage = filtered.slice(offset, offset + pageSize);
      setCharities(current => append ? [...current, ...nextPage] : nextPage);
      setProfileOffset(offset + nextPage.length);
      setProfilesHaveMore(offset + nextPage.length < filtered.length);
      if (requestSequence === directoryRequestSequenceRef.current) {
        setDirectoryInitialLoaded(true);
        setLoading(false);
        setLoadingMoreProfiles(false);
        directoryRequestRef.current = null;
        setApiError("directory", null);
      }
      return;
    }
    try {
      let url = `${API_BASE}/api/charities?limit=${pageSize + 1}&skip=${offset}&include_score=true&sort=${profileSort}`;
      if (searchTerm) url += `&search=${encodeURIComponent(searchTerm)}`;
      url += `&sources=${encodeURIComponent(selectedDataSources.join(","))}`;
      if (selectedTags.length > 0) {
        url += `&tags=${encodeURIComponent(selectedTags.join(","))}`;
      }
      if (selectedFoundationRegions.length > 0) {
        url += `&foundation_regions=${encodeURIComponent(selectedFoundationRegions.join(","))}`;
      }
      if (selectedRecipientRegions.length > 0) {
        url += `&funding_regions=${encodeURIComponent(selectedRecipientRegions.join(","))}`;
      }
      const minGiving = ANNUAL_GIVING_STEPS[annualGivingIndex];
      if (minGiving > 0) {
        url += `&min_annual_giving=${minGiving}`;
      }
      const maxGiving = optionalFilterAmount(maxAnnualGivingInput);
      if (maxGiving !== null) {
        url += `&max_annual_giving=${maxGiving}`;
      }
      const minAvgGrant = AVG_GRANT_SIZE_STEPS[avgGrantSizeIndex];
      if (minAvgGrant > 0) {
        url += `&min_avg_grant_size=${minAvgGrant}`;
      }
      const maxAvgGrant = optionalFilterAmount(maxAvgGrantSizeInput);
      if (maxAvgGrant !== null) {
        url += `&max_avg_grant_size=${maxAvgGrant}`;
      }

      const resp = await fetch(url, { credentials: "include", signal: controller.signal });
      if (requestSequence !== directoryRequestSequenceRef.current) return;
      if (resp.ok) {
        const data: Charity[] = await resp.json();
        if (requestSequence !== directoryRequestSequenceRef.current) return;
        const nextPage = data.slice(0, pageSize);
        setCharities(current => append ? [...current, ...nextPage] : nextPage);
        setProfileOffset(offset + nextPage.length);
        setProfilesHaveMore(data.length > pageSize);
        setApiError("directory", null);
      } else {
        setApiError("directory", `Directory request failed (${resp.status}).`);
      }
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      if (requestSequence === directoryRequestSequenceRef.current) {
        setApiError("directory", "The organization directory is temporarily unavailable.");
      }
    } finally {
      if (requestSequence === directoryRequestSequenceRef.current) {
        setDirectoryInitialLoaded(true);
        directoryRequestRef.current = null;
        if (append) {
          setLoadingMoreProfiles(false);
        } else {
          setLoading(false);
        }
      }
    }
  };

  const fetchCharityDetail = async (id: number, requestId: number, signal: AbortSignal) => {
    try {
      if (!isBffOnline) {
        // Mock fallback
        const mock = MOCK_CHARITIES.find(c => c.registered_charity_number === id);
        if (isCurrentProfileRequest(requestId)) {
          setSelectedCharityDetail(mock ? {
            registered_charity_number: id,
            suffix: 0,
            all_details: {
              charity_name: mock.charity_name,
              email: "info@netlight-charity.org.uk",
              phone: "+44 20 7946 0192",
              web: "https://www.netlight-charity.org.uk",
              address_line_one: "Netlight Amplify HQ",
              address_line_two: "123 Ash Avenue",
              address_line_three: "London",
              address_post_code: "EC1A 1BB",
              reg_status: "R"
            },
            financial_history: [
              { financial_period_end_date: "2024-12-31", income: mock?.latest_income, expenditure: mock?.latest_expenditure },
              { financial_period_end_date: "2023-12-31", income: mock?.latest_income == null ? null : mock.latest_income * 0.95, expenditure: mock?.latest_expenditure == null ? null : mock.latest_expenditure * 0.92 },
              { financial_period_end_date: "2022-12-31", income: mock?.latest_income == null ? null : mock.latest_income * 0.90, expenditure: mock?.latest_expenditure == null ? null : mock.latest_expenditure * 0.88 }
            ]
          } : null);
        }
        return;
      }
      const resp = await fetch(`${API_BASE}/api/charities/${id}`, {
        credentials: "include",
        signal,
      });
      if (!isCurrentProfileRequest(requestId)) return;
      if (resp.ok) {
        const data = await resp.json();
        if (!isCurrentProfileRequest(requestId)) return;
        const cachedSourceRecord = selectedSourceFunderProfileKeyRef.current
          ? sourceFunderProfilePayloadsRef.current.get(selectedSourceFunderProfileKeyRef.current)
          : undefined;
        const detail = cachedSourceRecord ? {
          ...data,
          ...cachedSourceRecord,
          all_details: { ...data.all_details, ...cachedSourceRecord.all_details },
        } : data;
        setSelectedCharityDetail(detail);
        setSelectedCharity(current => current?.registered_charity_number === id ? {
          ...current,
          charity_name: detail.all_details?.charity_name || current.charity_name,
          reg_status: detail.all_details?.reg_status || current.reg_status,
          reporting_status: detail.all_details?.reporting_status || current.reporting_status,
          latest_income: detail.all_details?.latest_income ?? current.latest_income,
          latest_expenditure: detail.all_details?.latest_expenditure ?? current.latest_expenditure,
        } : current);
      } else {
        const message = `Organization detail request failed (${resp.status}).`;
        setSelectedCharityDetail(null);
        setProfileSection(requestId, "detail", "error", message);
      }
    } catch (e) {
      if ((e as Error).name === "AbortError" || !isCurrentProfileRequest(requestId)) return;
      const message = "Organization details are temporarily unavailable.";
      setSelectedCharityDetail(null);
      setProfileSection(requestId, "detail", "error", message);
    } finally {
      finishProfileSection(requestId, "detail");
    }
  };

  const hydrateSourceFunderProfile = async (
    sourceFunderKey: string,
    profileId: number,
    requestId: number,
    signal: AbortSignal,
  ) => {
    if (!isBffOnline) return;
    const requestIsCurrent = () => (
      !signal.aborted
      && isCurrentProfileRequest(requestId)
      && selectedSourceFunderProfileKeyRef.current === sourceFunderKey
    );
    setProfileSection(requestId, "source_record", "loading");
    try {
      const queued = await fetch(
        `${API_BASE}/api/charities/grants/funders/${encodeURIComponent(sourceFunderKey)}/profile-cache`,
        {
          method: "POST",
          credentials: "include",
          headers: mutationHeaders("hydrate source-funder profile"),
          signal,
        },
      );
      if (!requestIsCurrent()) return;
      if (!queued.ok && queued.status !== 409) {
        const body = await queued.json().catch(() => ({}));
        throw new Error(body.detail || `Could not start profile hydration (${queued.status}).`);
      }

      for (let attempt = 0; attempt <= 20; attempt += 1) {
        const response = await fetch(
          `${API_BASE}/api/charities/grants/funders/${encodeURIComponent(sourceFunderKey)}/profile-cache`,
          { credentials: "include", signal },
        );
        if (!requestIsCurrent()) return;

        if (response.status === 404) {
          if (attempt === 20) throw new Error("Profile hydration did not become available in time.");
          await abortableDelay(450, signal, sourceHydrationTimerRef);
          continue;
        }

        const cache = await response.json();
        if (!requestIsCurrent()) return;
        if (!response.ok) throw new Error(cache.detail || `Profile hydration failed (${response.status}).`);
        if (cache.status === "pending") {
          if (attempt === 20) throw new Error("Profile hydration did not complete in time.");
          await abortableDelay(450, signal, sourceHydrationTimerRef);
          continue;
        }

        if (cache.status === "failed") {
          throw new Error(cache.error || "The local profile scraper record could not be loaded.");
        }
        if (cache.status !== "ready" || !cache.payload) {
          throw new Error("Profile hydration returned an unexpected state.");
        }

        const payload = cache.payload as Record<string, any>;
        sourceFunderProfilePayloadsRef.current.set(sourceFunderKey, payload);
        if (!requestIsCurrent()) return;
        setSelectedCharityDetail((current: any) => ({
          ...(current || {}),
          ...payload,
          all_details: { ...(current?.all_details || {}), ...(payload.all_details || {}) },
        }));
        setSelectedCharity((current: Charity | null) => current?.registered_charity_number === profileId ? {
          ...current,
          charity_name: payload.all_details?.charity_name || current.charity_name,
          reg_status: payload.all_details?.reg_status || current.reg_status,
          reporting_status: payload.all_details?.reporting_status || current.reporting_status,
          latest_income: payload.all_details?.latest_income ?? current.latest_income,
          latest_expenditure: payload.all_details?.latest_expenditure ?? current.latest_expenditure,
        } : current);
        finishProfileSection(requestId, "source_record");
        return;
      }
    } catch (error) {
      if ((error as Error).name === "AbortError" || !requestIsCurrent()) return;
      setProfileSection(
        requestId,
        "source_record",
        "error",
        (error as Error).message || "Profile data is temporarily unavailable.",
      );
    } finally {
      if (sourceHydrationTimerRef.current !== null && signal.aborted) {
        window.clearTimeout(sourceHydrationTimerRef.current);
        sourceHydrationTimerRef.current = null;
      }
    }
  };

  const saveNewsRun = (
    organization: Charity,
    briefing: NewsSummaryPayload,
    mode: "live" | "illustrative",
  ) => {
    const savedAt = new Date().toISOString();
    const run: SavedNewsRun = {
      ...briefing,
      organizationKey: newsOrganizationKey(organization),
      savedAt,
      mode,
    };
    setNewsRuns(current => [
      run,
      ...current.filter(item => item.organizationKey !== run.organizationKey),
    ].slice(0, 24));
  };

  const openSavedNewsRun = (run: SavedNewsRun) => {
    setNewsSummary({
      foundation: run.foundation,
      summary: run.summary,
      sources: run.sources,
      searched_weeks: run.searched_weeks,
      generated_at: run.generated_at,
    });
    setNewsRunMode(run.mode);
    setNewsError(null);
    setNewsProgressStep(null);
  };

  const toggleSavedNewsRun = (run: SavedNewsRun) => {
    if (newsSummary?.generated_at === run.generated_at) {
      setNewsSummary(null);
      setNewsRunMode(null);
      setNewsError(null);
      return;
    }
    openSavedNewsRun(run);
  };

  const cancelNewsResearch = (organizationKey: string | null, removeSavedRun = false) => {
    newsRequestSequenceRef.current += 1;
    newsAbortControllerRef.current?.abort();
    newsAbortControllerRef.current = null;
    setNewsSummary(null);
    setNewsError(null);
    setNewsLoading(false);
    setNewsProgressStep(null);
    setNewsRunMode(null);
    if (removeSavedRun && organizationKey) {
      setNewsRuns(current => current.filter(run => run.organizationKey !== organizationKey));
    }
  };

  const fetchFoundationNews = async (organization: Charity) => {
    const name = organization.charity_name;
    const organizationKey = newsOrganizationKey(organization);
    newsAbortControllerRef.current?.abort();
    const controller = new AbortController();
    newsAbortControllerRef.current = controller;
    const requestSequence = ++newsRequestSequenceRef.current;
    const isCurrentNewsRequest = () => (
      newsRequestSequenceRef.current === requestSequence
      && selectedNewsOrganizationKeyRef.current === organizationKey
      && !controller.signal.aborted
    );
    setNewsLoading(true);
    setNewsError(null);
    setNewsSummary(null);
    setNewsProgressStep("discovering");
    setNewsRunMode(null);

    if (!isBffOnline) {
      try {
        // Keep illustrative mode legible while preserving the same visible stages.
        await abortableDelay(350, controller.signal);
        if (!isCurrentNewsRequest()) return;
        setNewsProgressStep("reading");
        await abortableDelay(500, controller.signal);
        if (!isCurrentNewsRequest()) return;
        setNewsProgressStep("summarizing");
        await abortableDelay(550, controller.signal);
        if (!isCurrentNewsRequest()) return;
        const briefing: NewsSummaryPayload = {
          foundation: name,
          summary: `Here is a mock summary of recent news for "${name}". The foundation has been actively expanding its socio-economic support programs in the UK. They announced a new partnership with local food banks to address food insecurity. Furthermore, they are investing in digital transformation initiatives to streamline grant-making processes for small charities.`,
          sources: [
            { title: "Netlight News: Insecurity Partnership", link: "https://example.com/news1", source: "Netlight Post", published: "Mon, 20 Jul 2026 10:00:00 GMT" },
            { title: "Charity Digital: Streamlining Grants", link: "https://example.com/news2", source: "Charity Daily", published: "Sun, 19 Jul 2026 14:30:00 GMT" }
          ],
          searched_weeks: 4,
          generated_at: new Date().toISOString(),
        };
        setNewsSummary(briefing);
        setNewsRunMode("illustrative");
        saveNewsRun(organization, briefing, "illustrative");
      } catch (error) {
        if ((error as Error).name !== "AbortError" && isCurrentNewsRequest()) {
          setNewsError("News research could not be completed.");
        }
      } finally {
        if (isCurrentNewsRequest()) {
          setNewsProgressStep(null);
          setNewsLoading(false);
          if (newsAbortControllerRef.current === controller) newsAbortControllerRef.current = null;
        }
      }
      return;
    }

    try {
      const aliases = Array.from(new Set(selectedNewsAliases
        .map(alias => alias.trim())
        .filter(alias => alias && alias.localeCompare(name, undefined, { sensitivity: "accent" }) !== 0)));
      const query = new URLSearchParams();
      if (aliases.length) {
        query.set("aliases", aliases.join("|"));
        // A trading name can have sparse recent coverage. Search its full
        // published history, clearly retaining source dates in the briefing.
        query.set("lookback", "all");
      }
      const suffix = query.size ? `?${query.toString()}` : "";
      const resp = await fetch(`${API_BASE}/api/news/${encodeURIComponent(name)}/summary/stream${suffix}`, {
        credentials: "include",
        headers: { Accept: "text/event-stream" },
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) {
        const errorDetail = await resp.json().catch(() => null);
        throw new Error(errorDetail?.detail || `News research request failed (${resp.status}).`);
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let completed = false;
      const handleEvent = (message: string) => {
        const event = message.match(/^event:\s*(.+)$/m)?.[1]?.trim();
        const serialized = message.match(/^data:\s*(.+)$/m)?.[1];
        if (!event || !serialized) return;
        const payload = JSON.parse(serialized) as Record<string, unknown>;
        if (event === "progress") {
          const step = payload.step;
          if (isCurrentNewsRequest() && (step === "discovering" || step === "reading" || step === "summarizing")) setNewsProgressStep(step);
          return;
        }
        if (event === "error") throw new Error(typeof payload.detail === "string" ? payload.detail : "News research could not be completed.");
        if (event === "complete") {
          if (typeof payload.foundation !== "string" || typeof payload.summary !== "string" || !Array.isArray(payload.sources)) {
            throw new Error("News research returned an incomplete briefing.");
          }
          const briefing: NewsSummaryPayload = {
            foundation: payload.foundation,
            summary: payload.summary,
            sources: payload.sources as NewsSourceItem[],
            searched_weeks: typeof payload.searched_weeks === "number" ? payload.searched_weeks : 4,
            generated_at: typeof payload.generated_at === "string" ? payload.generated_at : new Date().toISOString(),
          };
          if (!isCurrentNewsRequest()) return;
          setNewsSummary(briefing);
          setNewsRunMode("live");
          saveNewsRun(organization, briefing, "live");
          completed = true;
        }
      };
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const messages = buffer.split("\n\n");
        buffer = messages.pop() || "";
        messages.forEach(handleEvent);
        if (done) break;
      }
      if (buffer.trim()) handleEvent(buffer);
      if (!completed) throw new Error("News research ended before a briefing was returned.");
    } catch (e: any) {
      if ((e as Error).name === "AbortError" || !isCurrentNewsRequest()) return;
      setNewsError(e?.message || "An error occurred while connecting to the news service.");
    } finally {
      if (isCurrentNewsRequest()) {
        setNewsLoading(false);
        setNewsProgressStep(null);
        if (newsAbortControllerRef.current === controller) newsAbortControllerRef.current = null;
      }
    }
  };

  const fetchMapData = async (
    forceOnline?: boolean,
    currencyOverride?: string,
    filtersOverride?: GrantMapFilters,
  ) => {
    const isOnline = forceOnline !== undefined ? forceOnline : isBffOnline;
    if (!isOnline) {
      mapRequestRef.current?.abort();
      setMapData(EMPTY_MAP);
      setMapLoading(false);
      return;
    }
    mapRequestRef.current?.abort();
    const controller = new AbortController();
    mapRequestRef.current = controller;
    setMapLoading(true);
    setMapError(null);
    const requestedCurrency = currencyOverride ?? grantAnalyticsCurrency;
    const requestedFilters = filtersOverride ?? mapFilters;
    const params = new URLSearchParams();
    if (requestedCurrency) params.set("currency", requestedCurrency);
    if (requestedFilters.search.trim()) params.set("search", requestedFilters.search.trim());
    if (requestedFilters.tags.length) params.set("tags", requestedFilters.tags.join(","));
    if (requestedFilters.foundationRegions.length) {
      params.set("foundation_regions", requestedFilters.foundationRegions.join(","));
    }
    if (requestedFilters.fundingRegions.length) {
      params.set("funding_regions", requestedFilters.fundingRegions.join(","));
    }
    if (requestedFilters.minAnnualGiving > 0) {
      params.set("min_annual_giving", String(requestedFilters.minAnnualGiving));
    }
    if (requestedFilters.minAvgGrantSize > 0) {
      params.set("min_avg_grant_size", String(requestedFilters.minAvgGrantSize));
    }
    const query = params.toString();
    try {
      const resp = await fetch(
        `${API_BASE}/api/charities/grants/map${query ? `?${query}` : ""}`,
        { credentials: "include", signal: controller.signal },
      );
      if (controller.signal.aborted || mapRequestRef.current !== controller) return;
      if (resp.ok) {
        const data: GrantMapResponse = await resp.json();
        setMapData(data);
      } else {
        setMapError(`Map-data request failed (${resp.status}).`);
      }
    } catch (e) {
      if ((e as Error).name === "AbortError" || mapRequestRef.current !== controller) return;
      setMapData(EMPTY_MAP);
      setMapError("Map data is temporarily unavailable.");
    } finally {
      if (mapRequestRef.current === controller) {
        mapRequestRef.current = null;
        setMapLoading(false);
      }
    }
  };

  const navigateApplication = (
    view: "overview" | "donors" | "research" | "registry" | "favorites" | "pipeline",
    mode: "push" | "replace" = "push",
  ) => {
    const query = new URLSearchParams(window.location.search);
    if (view === "overview") {
      query.delete("view");
      ["funder_country", "funder_sort", "funder_page", "donor_search", "donor_status", "donor"].forEach(key => query.delete(key));
      setActiveTab("overview");
    } else {
      query.set("view", view);
      if (view === "pipeline") {
        setActiveTab("admin");
      } else if (view === "favorites") {
        setActiveTab("favorites");
      } else {
        setActiveTab("directory");
        setDirectoryMode(view === "research" ? "profiles" : view === "registry" ? "registry" : "donors");
      }
    }
    const suffix = query.toString();
    window.history[mode === "push" ? "pushState" : "replaceState"](
      {}, "", `${window.location.pathname}${suffix ? `?${suffix}` : ""}`,
    );
    setMobileNavigationOpen(false);
  };

  const openOrganizationDirectoryFromMap = (filters: GrantMapFilters) => {
    setSearchTerm(filters.search);
    setSelectedTags(filters.tags);
    setSelectedFoundationRegions(filters.foundationRegions);
    setSelectedRecipientRegions(filters.fundingRegions);
    setAnnualGivingIndex(Math.max(0, ANNUAL_GIVING_STEPS.indexOf(filters.minAnnualGiving)));
    setAvgGrantSizeIndex(Math.max(0, AVG_GRANT_SIZE_STEPS.indexOf(filters.minAvgGrantSize)));
    setDirectoryHandoff(previous => ({
      version: previous.version + 1,
      query: filters.search,
      beneficiaryGeography: filters.fundingRegions.length === 1 ? filters.fundingRegions[0] : "",
    }));
    setSelectedCharity(null);
    navigateApplication("research");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const openSourceFundersFromMap = (
    selection: SourceFunderCountrySelection,
    grantFilters: OverviewFilters,
  ) => {
    const query = applyGrantScopeToParams(
      new URLSearchParams(window.location.search),
      {
        beneficiaryCountry: selection.countryCode,
        currency: grantFilters.currency || undefined,
        dateFrom: grantFilters.dateFrom || undefined,
        dateTo: grantFilters.dateTo || undefined,
        beneficiaryGeographies: grantFilters.beneficiaryGeographies,
        programmeAreas: grantFilters.programmeAreas,
        donor: grantFilters.donor,
        recipient: grantFilters.recipient,
        sources: selectedDataSources,
      },
      { persistEmptySources: true },
    );
    query.delete("funder_sort");
    query.delete("funder_page");
    query.delete("donor_search");
    query.delete("donor_status");
    query.delete("donor");
    if (grantFilters.granularity === "auto") query.delete("grant_granularity");
    else query.set("grant_granularity", grantFilters.granularity);
    query.set("view", "donors");
    window.history.pushState({}, "", `${window.location.pathname}?${query.toString()}`);
    setSelectedCharity(null);
    setDirectoryMode("donors");
    setActiveTab("directory");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const openLinkedDirectoryProfile = (profile: {
    charity_id: number;
    name: string | null;
    sourceFunderKey?: string | null;
    sourceFunderName?: string | null;
  }) => {
    profilePreviousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const sourceFunderKey = profile.sourceFunderKey || null;
    selectedSourceFunderProfileKeyRef.current = sourceFunderKey;
    setSelectedSourceFunderProfileKey(sourceFunderKey);
    setSelectedNewsAliases(profile.sourceFunderName?.trim() ? [profile.sourceFunderName.trim()] : []);
    setSelectedCharity({
      registered_charity_number: profile.charity_id,
      suffix: 0,
      link: "",
      charity_name: profile.name || "Linked organization",
      reg_status: "UNKNOWN",
      reporting_status: "",
      removal_reason: null,
      latest_income: null,
      latest_expenditure: null,
      programme_areas_source: [],
      programme_areas_inferred: [],
      geographic_focus_source: [],
      geographic_focus_inferred: [],
      organization_type: "unknown",
      source_names: [],
      transaction_coverage: "unknown",
    });
  };

  const resetDirectoryFilters = () => {
    setSearchTerm("");
    setOrganizationSuggestions([]);
    setSuggestionsOpen(false);
    setSelectedTags([]);
    setSelectedFoundationRegions([]);
    setSelectedRecipientRegions([]);
    setAnnualGivingIndex(0);
    setMaxAnnualGivingInput("");
    setAvgGrantSizeIndex(0);
    setMaxAvgGrantSizeInput("");
    setProfileOffset(0);
    setProfilesHaveMore(false);
  };

  const profileFavoriteKey = (profile: Charity) =>
    `profile:${profile.registered_charity_number}:${profile.source_record_id || ""}`;

  const toggleFavoriteProfile = (profile: Charity) => {
    const key = profileFavoriteKey(profile);
    setFavorites(current => current.profiles.some(item => item.key === key)
      ? { ...current, profiles: current.profiles.filter(item => item.key !== key) }
      : { ...current, profiles: [{ key, profile, savedAt: Date.now() }, ...current.profiles] });
  };

  const toggleFavoriteDonor = (donor: FavoriteDonorPayload) => {
    setFavorites(current => current.donors.some(item => item.key === donor.key)
      ? { ...current, donors: current.donors.filter(item => item.key !== donor.key) }
      : { ...current, donors: [{ ...donor, savedAt: Date.now() }, ...current.donors] });
  };

  const currentResearchFavorite = (): FavoriteResearchView => {
    const filters: FavoriteResearchView["filters"] = {
      searchTerm,
      selectedTags,
      selectedFoundationRegions,
      selectedRecipientRegions,
      annualGivingIndex,
      maxAnnualGivingInput,
      avgGrantSizeIndex,
      maxAvgGrantSizeInput,
      profileSort,
    };
    const activeTerms = [
      searchTerm.trim(),
      selectedTags[0],
      selectedFoundationRegions[0],
      selectedRecipientRegions[0],
    ].filter(Boolean);
    return {
      key: `research:${JSON.stringify(filters)}`,
      label: activeTerms.length ? `Organization Research · ${activeTerms.join(" · ")}` : "Organization Research",
      filters,
      savedAt: Date.now(),
    };
  };

  const researchFavorite = currentResearchFavorite();
  const researchViewIsFavorite = favorites.researchViews.some(view => view.key === researchFavorite.key);

  const toggleCurrentResearchFavorite = () => {
    const view = currentResearchFavorite();
    setFavorites(current => current.researchViews.some(item => item.key === view.key)
      ? { ...current, researchViews: current.researchViews.filter(item => item.key !== view.key) }
      : { ...current, researchViews: [{ ...view, savedAt: Date.now() }, ...current.researchViews] });
  };

  const removeFavoriteResearch = (key: string) => {
    setFavorites(current => ({ ...current, researchViews: current.researchViews.filter(item => item.key !== key) }));
  };

  const openFavoriteProfile = (favorite: FavoriteProfile) => {
    profilePreviousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    setSelectedCharity(favorite.profile);
  };

  const toggleFavoriteDonorRequest = (request: FavoriteDonorRequestPayload) => {
    setFavorites(current => current.donorRequests.some(item => item.key === request.key)
      ? { ...current, donorRequests: current.donorRequests.filter(item => item.key !== request.key) }
      : { ...current, donorRequests: [{ ...request, savedAt: Date.now() }, ...current.donorRequests] });
  };

  const toggleFavoriteGrantExplorer = (favorite: FavoriteGrantExplorerPayload) => {
    setFavorites(current => current.grantExplorers.some(item => item.key === favorite.key)
      ? { ...current, grantExplorers: current.grantExplorers.filter(item => item.key !== favorite.key) }
      : { ...current, grantExplorers: [{ ...favorite, savedAt: Date.now() }, ...current.grantExplorers] });
  };

  const restoreSourcesFromFavoriteRoute = (route: string) => {
    const params = new URLSearchParams(route.startsWith("?") ? route.slice(1) : route);
    if (!params.has("grant_sources")) return;
    const selected = new Set((params.get("grant_sources") || "").split(",").filter(Boolean));
    setDataSourceSelections(Object.fromEntries(dataSourceNames.map(source => [source, selected.has(source)])));
  };

  const openFavoriteDonorWorkspace = (workspace: FavoriteDonorWorkspace) => {
    const route = workspace.item.route;
    favoriteReturnUrlRef.current = `${window.location.pathname}${window.location.search}`;
    favoriteReturnScrollRef.current = window.scrollY;
    restoreSourcesFromFavoriteRoute(route);
    const query = new URLSearchParams(route.startsWith("?") ? route.slice(1) : route);
    query.set("view", "favorites");
    if (workspace.kind === "donor") query.set("donor", workspace.item.key);
    else query.delete("donor");
    window.history.pushState({}, "", `${window.location.pathname}?${query.toString()}`);
    setSelectedCharity(null);
    setFavoriteGrantExplorer(null);
    setFavoriteDonorWorkspace(workspace);
    setActiveTab("favorites");
    setMobileNavigationOpen(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const closeFavoriteDonorWorkspace = () => {
    const returnUrl = favoriteReturnUrlRef.current || `${window.location.pathname}?view=favorites`;
    const scrollPosition = favoriteReturnScrollRef.current;
    setFavoriteDonorWorkspace(null);
    window.history.replaceState({}, "", returnUrl);
    window.requestAnimationFrame(() => window.scrollTo({ top: scrollPosition, behavior: "smooth" }));
  };

  const openFavoriteDonor = (favorite: FavoriteDonorPayload) => {
    openFavoriteDonorWorkspace({ kind: "donor", item: favorite });
  };

  const openFavoriteDonorRequest = (request: FavoriteDonorRequestPayload) => {
    openFavoriteDonorWorkspace({ kind: "request", item: request });
  };

  const openFavoriteGrantExplorer = (favorite: FavoriteGrantExplorerPayload) => {
    favoriteReturnUrlRef.current = `${window.location.pathname}${window.location.search}`;
    favoriteReturnScrollRef.current = window.scrollY;
    restoreSourcesFromFavoriteRoute(favorite.route);
    const query = new URLSearchParams(favorite.route.startsWith("?") ? favorite.route.slice(1) : favorite.route);
    query.set("view", "favorites");
    window.history.pushState({}, "", `${window.location.pathname}?${query.toString()}`);
    setSelectedCharity(null);
    setFavoriteDonorWorkspace(null);
    setFavoriteGrantExplorer(favorite);
    setActiveTab("favorites");
    setMobileNavigationOpen(false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const closeFavoriteGrantExplorer = () => {
    const returnUrl = favoriteReturnUrlRef.current || `${window.location.pathname}?view=favorites`;
    const scrollPosition = favoriteReturnScrollRef.current;
    setFavoriteGrantExplorer(null);
    window.history.replaceState({}, "", returnUrl);
    window.requestAnimationFrame(() => window.scrollTo({ top: scrollPosition, behavior: "smooth" }));
  };

  const openFavoriteResearch = (favorite: FavoriteResearchView) => {
    const { filters } = favorite;
    setSearchTerm(filters.searchTerm);
    setSelectedTags(filters.selectedTags);
    setSelectedFoundationRegions(filters.selectedFoundationRegions);
    setSelectedRecipientRegions(filters.selectedRecipientRegions);
    setAnnualGivingIndex(filters.annualGivingIndex);
    setMaxAnnualGivingInput(filters.maxAnnualGivingInput);
    setAvgGrantSizeIndex(filters.avgGrantSizeIndex);
    setMaxAvgGrantSizeInput(filters.maxAvgGrantSizeInput);
    setProfileSort(filters.profileSort);
    setProfileOffset(0);
    setSelectedCharity(null);
    navigateApplication("research");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const fetchGrantAnalytics = async (forceOnline?: boolean, currencyOverride?: string) => {
    const isOnline = forceOnline !== undefined ? forceOnline : isBffOnline;
    if (!isOnline) {
      grantAnalyticsRequestRef.current?.abort();
      setGrantTrends(null);
      setGrantThemes(null);
      setGrantAnalyticsError("Grant analytics require the cached SQLite transaction database.");
      return;
    }
    grantAnalyticsRequestRef.current?.abort();
    const controller = new AbortController();
    grantAnalyticsRequestRef.current = controller;
    setGrantAnalyticsLoading(true);
    setGrantAnalyticsError(null);
    const requestedCurrency = currencyOverride || grantAnalyticsCurrency;
    const currencyQuery = requestedCurrency
      ? `&currency=${encodeURIComponent(requestedCurrency)}`
      : "";
    try {
      const [trendsResponse, themesResponse] = await Promise.all([
        fetch(`${API_BASE}/api/charities/grants/trends?months=24${currencyQuery}`, {
          credentials: "include",
          signal: controller.signal,
        }),
        fetch(`${API_BASE}/api/charities/grants/themes?${currencyQuery.slice(1)}`, {
          credentials: "include",
          signal: controller.signal,
        })
      ]);
      if (controller.signal.aborted || grantAnalyticsRequestRef.current !== controller) return;
      if (!trendsResponse.ok || !themesResponse.ok) {
        setGrantAnalyticsError(
          `Grant analytics request failed (${trendsResponse.status}/${themesResponse.status}).`
        );
        return;
      }
      const trends: GrantTrendsResponse = await trendsResponse.json();
      const themes: GrantThemesResponse = await themesResponse.json();
      setGrantTrends(trends);
      setGrantThemes(themes);
      const resolvedCurrency = trends.currency || themes.currency;
      if (resolvedCurrency) setGrantAnalyticsCurrency(resolvedCurrency);
    } catch (error) {
      if ((error as Error).name === "AbortError" || grantAnalyticsRequestRef.current !== controller) return;
      setGrantAnalyticsError("Grant analytics are temporarily unavailable.");
      setGrantTrends(null);
      setGrantThemes(null);
    } finally {
      if (grantAnalyticsRequestRef.current === controller) {
        grantAnalyticsRequestRef.current = null;
        setGrantAnalyticsLoading(false);
      }
    }
  };

  const fetchCharityGrants = async (id: number, requestId: number, signal: AbortSignal) => {
    try {
      if (!isBffOnline) {
        if (isCurrentProfileRequest(requestId)) {
          setCharityGrants([]);
          setGrantStatus("transaction_data_unavailable");
        }
        return;
      }
      const resp = await fetch(`${API_BASE}/api/charities/${id}/grants`, {
        credentials: "include",
        signal,
      });
      if (!isCurrentProfileRequest(requestId)) return;
      if (resp.ok) {
        const data = await resp.json();
        if (!isCurrentProfileRequest(requestId)) return;
        setCharityGrants(data.grants || []);
        setGrantStatus(data.status || "data_unavailable");
      } else {
        const message = `Grant transaction request failed (${resp.status}).`;
        setCharityGrants([]);
        setGrantStatus("request_failed");
        setProfileSection(requestId, "grants", "error", message);
      }
    } catch (e) {
      if ((e as Error).name === "AbortError" || !isCurrentProfileRequest(requestId)) return;
      const message = "Grant transactions are temporarily unavailable.";
      setCharityGrants([]);
      setGrantStatus("request_failed");
      setProfileSection(requestId, "grants", "error", message);
    } finally {
      finishProfileSection(requestId, "grants");
    }
  };

  const fetchSankeyData = async (id: number, requestId: number, signal: AbortSignal) => {
    try {
      if (!isBffOnline) {
        if (isCurrentProfileRequest(requestId)) {
          setSankeyData({
            status: "transaction_data_unavailable",
            nodes: [],
            links: [],
            currency: null,
            excludedCount: 0,
          });
        }
        return;
      }
      const resp = await fetch(`${API_BASE}/api/charities/${id}/sankey`, {
        credentials: "include",
        signal,
      });
      if (!isCurrentProfileRequest(requestId)) return;
      if (resp.ok) {
        const data = await resp.json();
        if (!isCurrentProfileRequest(requestId)) return;
        // Parse names directly to index integers for Recharts Sankey.
        const nodeMap = new Map();
        data.nodes.forEach((n: any, idx: number) => nodeMap.set(n.id, idx));
        const formattedLinks = data.links.map((l: any) => ({
          source: nodeMap.get(l.source),
          target: nodeMap.get(l.target),
          value: l.value,
          grantCount: l.grant_count,
        }));

        setSankeyData({
          status: data.status,
          nodes: data.nodes.map((n: any) => ({ id: n.id, name: n.label, role: n.role })),
          links: formattedLinks,
          currency: data.metadata?.selected_currency || null,
          excludedCount: data.metadata?.excluded_grant_count || 0,
        });
      } else {
        const message = `Funding relationship request failed (${resp.status}).`;
        setSankeyData({ status: "request_failed", nodes: [], links: [], currency: null, excludedCount: 0 });
        setProfileSection(requestId, "relationships", "error", message);
      }
    } catch (e) {
      if ((e as Error).name === "AbortError" || !isCurrentProfileRequest(requestId)) return;
      const message = "Grant-flow data is temporarily unavailable.";
      setSankeyData({ status: "request_failed", nodes: [], links: [], currency: null, excludedCount: 0 });
      setProfileSection(requestId, "relationships", "error", message);
    } finally {
      finishProfileSection(requestId, "relationships");
    }
  };

  const fetchScoreData = async (id: number, requestId: number, signal: AbortSignal) => {
    try {
      if (!isBffOnline) {
        if (isCurrentProfileRequest(requestId)) setScoreData(null);
        return;
      }
      const resp = await fetch(`${API_BASE}/api/charities/${id}/score`, {
        method: "GET",
        credentials: "include",
        signal,
      });
      if (!isCurrentProfileRequest(requestId)) return;
      if (resp.ok) {
        const data = await resp.json();
        if (!isCurrentProfileRequest(requestId)) return;
        setScoreData(data);
      } else {
        const message = `Experimental score request failed (${resp.status}).`;
        setScoreData(null);
        setProfileSection(requestId, "score", "error", message);
      }
    } catch (e) {
      if ((e as Error).name === "AbortError" || !isCurrentProfileRequest(requestId)) return;
      const message = "The experimental relevance score is temporarily unavailable.";
      setScoreData(null);
      setProfileSection(requestId, "score", "error", message);
    } finally {
      finishProfileSection(requestId, "score");
    }
  };

  const clearActiveProfileSafely = () => {
    // Invalidate first so no late response can re-open or repopulate the
    // profile after the user has returned to the stable directory view.
    profileRequestSequenceRef.current += 1;
    profileAbortControllerRef.current?.abort();
    profileAbortControllerRef.current = null;
    cancelNewsResearch(selectedNewsOrganizationKeyRef.current);
    selectedSourceFunderProfileKeyRef.current = null;
    setSelectedSourceFunderProfileKey(null);
    setSelectedNewsAliases([]);
    setSelectedCharity(null);
    setSelectedCharityDetail(null);
    setCharityGrants([]);
    setGrantStatus("data_unavailable");
    setSankeyData(null);
    setScoreData(null);
    setProfileLoading(IDLE_PROFILE_LOADING);
    setNewsSummary(null);
    setNewsError(null);
    setNewsLoading(false);
    setNewsProgressStep(null);
    setNewsRunMode(null);
    setApiError("source_reset", null);
    const previousFocus = profilePreviousFocusRef.current;
    profilePreviousFocusRef.current = null;
    window.requestAnimationFrame(() => previousFocus?.focus());
  };

  useEffect(() => {
    if (!selectedCharity) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        clearActiveProfileSafely();
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    window.requestAnimationFrame(() => profileModalRef.current?.focus());
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [selectedCharity]);

  const resetActiveSourceFunderToObserved = async () => {
    const target = activeSourceFunder;
    if (!target || !isBffOnline) {
      // The NL control is also available while viewing an ordinary profile.
      // In that case there is no source link to mutate, but its latest local
      // news research still belongs to the profile being safely cleared.
      cancelNewsResearch(selectedNewsOrganizationKeyRef.current, true);
      clearActiveProfileSafely();
      return;
    }

    setSourceFunderResetPending(true);
    try {
      const response = await fetch(
        `${API_BASE}/api/charities/grants/funders/${encodeURIComponent(target.sourceFunderKey)}/reset-to-observed`,
        {
          method: "POST",
          credentials: "include",
          headers: mutationHeaders("reset source funder to observed-only"),
        },
      );
      const body = await response.json();
      if (!response.ok) {
        throw new Error(body.detail || `Could not reset ${target.displayName} to observed-only (${response.status}).`);
      }
      window.dispatchEvent(new CustomEvent("source-funder-reset-to-observed", {
        detail: { sourceFunderKey: target.sourceFunderKey },
      }));
      sourceFunderProfilePayloadsRef.current.delete(target.sourceFunderKey);
      // News briefings are derived from this profile link and are stored only
      // in this browser.  Reset removes the matching latest briefing as well
      // as cancelling a search that could otherwise finish after the reset.
      cancelNewsResearch(selectedNewsOrganizationKeyRef.current, true);
      setApiError("source_reset", null);
      setActiveSourceFunder(null);
      clearActiveProfileSafely();
    } catch (error) {
      setApiError("source_reset", (error as Error).message || `Could not reset ${target.displayName} to observed-only.`);
    } finally {
      setSourceFunderResetPending(false);
    }
  };

  const fetchPipelineStatus = async (signal?: AbortSignal) => {
    if (!isBffOnline) return;
    try {
      const resp = await fetch(`${API_BASE}/api/admin/pipeline/status`, {
        credentials: "include",
        signal,
      });
      if (signal?.aborted) return;
      if (resp.ok) {
        const data = await resp.json();
        setPipelineStatus(data);
      }

      const logResp = await fetch(`${API_BASE}/api/admin/pipeline/logs`, {
        credentials: "include",
        signal,
      });
      if (signal?.aborted) return;
      if (logResp.ok) {
        const logData = await logResp.json();
        setLogs(logData.logs || "System idle.\n");
      }
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setLogs(previous => `${previous}\n[Error] Pipeline metrics are temporarily unavailable.\n`);
    }
  };

  const triggerPipeline = async (source: string) => {
    setIsTriggering(true);

    // Enforce max limit of 100 on full runs
    if (source === "full_run" && pipelineLimit > 100) {
      alert("Limit für Full Runs liegt bei maximal 100, um API-Sperren zu verhindern.");
      setIsTriggering(false);
      return;
    }

    const regNumbers = pipelineIds ? pipelineIds.split(",").map(id => parseInt(id.trim())).filter(id => !isNaN(id)) : undefined;

    if (!isBffOnline) {
      // Simulate run in logs
      setLogs(prev => prev + `\n[Simulating] Triggered run mode: ${source} (Limit: ${pipelineLimit}, Fresh: ${pipelineFresh ? "Yes" : "No"}, Impressum: ${enableImpressum ? "Yes" : "No"})...\n`);
      let count = 1;
      const interval = setInterval(() => {
        setLogs(prev => prev + `[Step ${count}/3] Crawling raw data seeds...\n`);
        count++;
        if (count > 3) {
          clearInterval(interval);
          setLogs(prev => prev + `[SUCCESS] Database seeded successfully (simulation offline).\n`);
          setIsTriggering(false);
        }
      }, 1000);
      return;
    }
    try {
      const resp = await fetch(`${API_BASE}/api/admin/pipeline/trigger`, {
        method: "POST",
        headers: mutationHeaders(`trigger ${source} pipeline`, true),
        body: JSON.stringify({
          source: source,
          limit: source === "quick_consolidate" ? undefined : pipelineLimit,
          fresh: source === "quick_consolidate" ? undefined : pipelineFresh,
          search_term: pipelineSearch ? pipelineSearch.trim() : undefined,
          reg_numbers: regNumbers && regNumbers.length > 0 ? regNumbers : undefined,
          skip_contact_crawler: !enableImpressum
        }),
        credentials: "include"
      });
      if (resp.ok) {
        const data = await resp.json();
        setPipelineStatus(data);
        setLogs(prev => prev + `\n[System] Predefined execution triggered successfully: ${source} (Limit: ${pipelineLimit}, Fresh: ${pipelineFresh ? "Yes" : "No"}, Impressum: ${enableImpressum ? "Yes" : "No"})\n`);
      } else {
        const errorData = await resp.json();
        alert(`Failed to trigger: ${errorData.detail || "Unknown error"}`);
      }
    } catch (e) {
      setLogs(previous => `${previous}\n[Error] Pipeline trigger failed: ${(e as Error).message || "Unknown error"}\n`);
    } finally {
      setIsTriggering(false);
    }
  };


  const formatCurrency = (val: number | null | undefined, currency = "GBP") => {
    if (val === null || val === undefined || !Number.isFinite(val)) return "Unavailable";
    const compact = Math.abs(val) >= 1000;
    try {
      return new Intl.NumberFormat("en-GB", {
        style: "currency",
        currency,
        notation: compact ? "compact" : "standard",
        maximumFractionDigits: compact ? 1 : 0,
      }).format(val);
    } catch {
      return `${currency} ${val.toLocaleString("en-GB")}`;
    }
  };

  const grantAnalyticsCurrencies = Array.from(new Set([
    ...(grantTrends?.available_currencies || []),
    ...(grantThemes?.available_currencies || [])
  ])).sort();
  const classificationPercentage = grantThemes?.classification_coverage.classified_percentage ?? 0;
  const unknownTrendMonths = grantTrends?.items.filter(
    item => item.coverage_status === "unknown"
  ).length ?? 0;
  const latestAnalyticsRefresh = grantTrends?.last_refreshed_at || grantThemes?.last_refreshed_at;
  const analyticsAreStale = Boolean(
    latestAnalyticsRefresh
    && Date.now() - Date.parse(latestAnalyticsRefresh) > 30 * 24 * 60 * 60 * 1000
  );
  const profileFilterCount = Number(Boolean(searchTerm.trim()))
    + selectedTags.length
    + selectedFoundationRegions.length
    + selectedRecipientRegions.length
    + Number(annualGivingIndex > 0)
    + Number(Boolean(maxAnnualGivingInput.trim()))
    + Number(avgGrantSizeIndex > 0)
    + Number(Boolean(maxAvgGrantSizeInput.trim()));
  const activeHeaderState: HeaderContextState = activeTab === "overview"
    ? {
        filterCount: overviewFilterCount,
        resetDisabled: overviewFilterCount === 0,
        filtersExpanded: overviewFiltersOpen,
      }
    : activeTab === "directory" && directoryMode === "donors"
      ? donorHeaderState
      : activeTab === "directory" && directoryMode === "registry"
        ? registryHeaderState
      : activeTab === "directory" && directoryMode === "profiles"
        ? {
            filterCount: profileFilterCount,
            resetDisabled: profileFilterCount === 0,
            filtersExpanded: profileFiltersOpen,
          }
        : { filterCount: 0, resetDisabled: true, filtersExpanded: false };

  const openContextFilters = () => {
    if (activeTab === "overview") {
      window.dispatchEvent(new Event("overview-open-filters"));
    } else if (activeTab === "directory" && directoryMode === "donors") {
      window.dispatchEvent(new Event("donor-directory-open-filters"));
    } else if (activeTab === "directory" && directoryMode === "profiles") {
      setProfileFiltersOpen(true);
    } else if (activeTab === "directory" && directoryMode === "registry") {
      window.dispatchEvent(new Event("registry-open-filters"));
    }
  };

  const resetContext = () => {
    if (activeTab === "overview") {
      window.dispatchEvent(new Event("overview-reset-filters"));
    } else if (activeTab === "directory" && directoryMode === "donors") {
      window.dispatchEvent(new Event("donor-directory-reset"));
    } else if (activeTab === "directory" && directoryMode === "profiles") {
      resetDirectoryFilters();
      clearActiveProfileSafely();
    } else if (activeTab === "directory" && directoryMode === "registry") {
      window.dispatchEvent(new Event("registry-reset-filters"));
    }
  };

  return (
    <div className={`app-container${sidebarCollapsed ? " sidebar-collapsed" : ""}${mobileNavigationOpen ? " sidebar-drawer-open" : ""}`}>
      <a className="skip-link" href="#main-content">Skip to main content</a>
      {/* Sidebar Navigation */}
      <aside className="sidebar">
        <div>
          <div className="sidebar-logo">
            <img src={amplifyLogo} alt="Amplify" />
            {!isBffOnline && <span style={{ fontSize: "10px", color: "var(--nl-sunny)", background: "var(--nl-sunny-glow)", padding: "2px 6px", borderRadius: "4px" }}>Offline</span>}
            <button
              type="button"
              className="sidebar-toggle"
              onClick={() => setSidebarCollapsed(current => !current)}
              aria-label={sidebarCollapsed ? "Expand navigation" : "Collapse navigation"}
            >
              {sidebarCollapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
            </button>
          </div>

          <nav className="sidebar-nav">
            <button
              className={`nav-item ${activeTab === "overview" ? "active" : ""}`}
              aria-current={activeTab === "overview" ? "page" : undefined}
              onClick={() => navigateApplication("overview")}
              title="Overview"
            >
              <TrendingUp size={18} />
              <span>Overview</span>
            </button>
            <button
              className={`nav-item ${activeTab === "directory" ? "active" : ""}`}
              aria-current={activeTab === "directory" ? "page" : undefined}
              onClick={() => navigateApplication("donors")}
              title="Donor Directory"
            >
              <Building2 size={18} />
              <span>Donor Directory</span>
            </button>
            <button
              className={`nav-item ${activeTab === "favorites" ? "active" : ""}`}
              aria-current={activeTab === "favorites" ? "page" : undefined}
              onClick={() => navigateApplication("favorites")}
              title="Favorites"
            >
              <Star size={18} />
              <span>Favorites</span>
            </button>
            <button
              className={`nav-item ${activeTab === "admin" ? "active" : ""}`}
              aria-current={activeTab === "admin" ? "page" : undefined}
              onClick={() => navigateApplication("pipeline")}
              title="Pipeline Monitor"
            >
              <Terminal size={18} />
              <span>Pipeline Monitor</span>
            </button>
          </nav>
        </div>

        <div className="sidebar-footer">
          <div className="user-profile">
            <button
              type="button"
              className="user-avatar user-avatar-reset"
              onClick={() => void resetActiveSourceFunderToObserved()}
              disabled={sourceFunderResetPending}
              title={activeSourceFunder ? `Reset ${activeSourceFunder.displayName} to observed-only` : "Safely clear the active organization profile"}
              aria-label={activeSourceFunder ? `Remove the linked profile for ${activeSourceFunder.displayName} while retaining its observed grants` : "Safely clear the active organization profile and cancel its background loading"}
            >{sourceFunderResetPending ? <LoaderCircle className="user-avatar-reset-spinner" size={17} aria-hidden="true" /> : "NL"}</button>
            <div className="user-info">
              <span className="user-name">Netlight Guest</span>
              <span className="user-role">Administrator</span>
            </div>
          </div>
          {activeTab === "admin" && <button className="sidebar-health-check" onClick={() => checkBffHealth()}>
            <Activity size={15} />
            <span>Check backend connection</span>
          </button>}
        </div>
      </aside>
      {mobileNavigationOpen && <button type="button" className="sidebar-backdrop" aria-label="Close navigation" onClick={() => setMobileNavigationOpen(false)} />}

      {/* Main Container Window */}
      <main id="main-content" className="main-content" tabIndex={-1}>
        <AppHeader
          filterCount={activeHeaderState.filterCount}
          filtersExpanded={activeHeaderState.filtersExpanded}
          filtersDisabled={activeTab === "admin" || activeTab === "favorites"}
          resetDisabled={activeHeaderState.resetDisabled}
          dataSources={dataSourceNames}
          selectedDataSources={selectedDataSources}
          online={isBffOnline}
          onOpenNavigation={() => setMobileNavigationOpen(true)}
          onOpenFilters={openContextFilters}
          onReset={resetContext}
          onToggleDataSource={toggleDataSource}
        />

        {/* Dynamic Pages */}
        <div className="page-container">
          {initialLoading && (
            <div className="data-notice data-notice-info connection-status" role="status" aria-live="polite">
              <LoaderCircle className="spin" size={16} aria-hidden="true" /> Connecting to the local data service. Available panels remain usable while readiness is checked.
            </div>
          )}
          {visibleApiErrors.length > 0 && (
            <div className="data-notice data-notice-error" role="status">
              {visibleApiErrors.join(" ")}
            </div>
          )}
          {!initialLoading && !isBffOnline && (
            <div className="data-notice data-notice-warning" role="status">
              Illustrative prototype mode — displayed values are local examples, not live source data.
            </div>
          )}
          {/* TAB 1: OVERVIEW */}
          {activeTab === "overview" && (
            <Suspense fallback={<div className="route-loading" role="status"><LoaderCircle className="spin" size={22} /> Loading landscape overview…</div>}>
              <OverviewDashboard
                apiBase={API_BASE}
                online={isBffOnline}
                selectedSources={selectedDataSources}
                onOpenOrganizationDirectory={openOrganizationDirectoryFromMap}
                onOpenProfile={(profileId, profileName) => openLinkedDirectoryProfile({ charity_id: profileId, name: profileName })}
                onSearchOrganization={(organizationName) => openOrganizationDirectoryFromMap({ ...EMPTY_GRANT_MAP_FILTERS, search: organizationName })}
                onExploreSourceFunders={openSourceFundersFromMap}
                favoriteGrantExplorerKeys={favorites.grantExplorers.map(favorite => favorite.key)}
                onToggleFavoriteGrantExplorer={toggleFavoriteGrantExplorer}
              />
            </Suspense>
          )}

          {activeTab === "favorites" && (
            favoriteGrantExplorer ? (
              <Suspense fallback={<div className="route-loading" role="status"><LoaderCircle className="spin" size={22} /> Loading saved grant view…</div>}>
                <OverviewDashboard
                  key={`favorite-explorer:${favoriteGrantExplorer.key}`}
                  apiBase={API_BASE}
                  online={isBffOnline}
                  selectedSources={selectedDataSources}
                  onOpenOrganizationDirectory={openOrganizationDirectoryFromMap}
                  onOpenProfile={(profileId, profileName) => openLinkedDirectoryProfile({ charity_id: profileId, name: profileName })}
                  onSearchOrganization={(organizationName) => openOrganizationDirectoryFromMap({ ...EMPTY_GRANT_MAP_FILTERS, search: organizationName })}
                  onExploreSourceFunders={openSourceFundersFromMap}
                  favoriteGrantExplorerKeys={favorites.grantExplorers.map(favorite => favorite.key)}
                  onToggleFavoriteGrantExplorer={toggleFavoriteGrantExplorer}
                  presentation="favorite-explorer"
                  initialDrilldown={favoriteGrantExplorer.selection}
                  onBackToFavorites={closeFavoriteGrantExplorer}
                />
              </Suspense>
            ) : favoriteDonorWorkspace ? (
              <Suspense fallback={<div className="route-loading"><LoaderCircle className="spin" size={22} /> Loading saved donor view…</div>}>
                <DonorDirectoryPage
                  key={`${favoriteDonorWorkspace.kind}:${favoriteDonorWorkspace.item.key}`}
                  apiBase={API_BASE}
                  online={isBffOnline}
                  selectedSources={selectedDataSources}
                  onHeaderStateChange={setDonorHeaderState}
                  onBackToLandscape={() => closeFavoriteDonorWorkspace()}
                  onOpenOrganizationResearch={() => navigateApplication("research")}
                  onOpenRegistrySearch={() => navigateApplication("registry")}
                  onOpenProfile={(profileId, profileName, sourceFunderKey, sourceFunderName) => openLinkedDirectoryProfile({
                    charity_id: profileId,
                    name: profileName,
                    sourceFunderKey,
                    sourceFunderName,
                  })}
                  favoriteDonorKeys={favorites.donors.map(donor => donor.key)}
                  onToggleFavoriteDonor={toggleFavoriteDonor}
                  favoriteDonorRequestKeys={favorites.donorRequests.map(request => request.key)}
                  onToggleFavoriteDonorRequest={toggleFavoriteDonorRequest}
                  presentation={favoriteDonorWorkspace.kind === "donor" ? "favorite-donor" : "favorite-request"}
                  onBackToFavorites={closeFavoriteDonorWorkspace}
                />
              </Suspense>
            ) : (
              <section className="favorites-page">
                <div className="page-introduction favorites-introduction">
                  <div>
                    <span className="page-eyebrow">Personal workspace</span>
                    <h2>Favorites</h2>
                    <p>Keep organizations, observed donors, grant explorations, donor requests, and research configurations you want to revisit.</p>
                  </div>
                </div>

                {favorites.profiles.length + favorites.donors.length + favorites.donorRequests.length + favorites.researchViews.length + favorites.grantExplorers.length === 0 ? (
                  <div className="glass-card favorites-empty-state">
                    <Star size={22} />
                    <h3>No favorites yet</h3>
                    <p>Use a star on an organization, observed donor, grant exploration, donor request, or research view to keep it here.</p>
                  </div>
                ) : (
                  <div className="favorites-sections">
                    {(favorites.profiles.length > 0 || favorites.donors.length > 0) && (
                      <section className="favorites-section" aria-labelledby="favorite-organizations-heading">
                        <div className="favorites-section-heading">
                          <div><span className="page-eyebrow">Organizations</span><h3 id="favorite-organizations-heading">Pinned organizations & donors</h3></div>
                          <span>{favorites.profiles.length + favorites.donors.length}</span>
                        </div>
                        <div className="favorites-grid">
                          {favorites.profiles.map(favorite => (
                            <article className="glass-card favorite-card" key={favorite.key}>
                              <button type="button" className="favorite-card-open" onClick={() => openFavoriteProfile(favorite)}>
                                <span className="favorite-card-type">Organization profile</span>
                                <strong>{favorite.profile.charity_name}</strong>
                                <small>{[favorite.profile.primary_source || "Organization data", favorite.profile.headquarters_country].filter(Boolean).join(" · ")}</small>
                              </button>
                              <button type="button" className="favorite-toggle is-favorite" aria-label={`Remove ${favorite.profile.charity_name} from favorites`} onClick={() => toggleFavoriteProfile(favorite.profile)}><Star size={16} fill="currentColor" /></button>
                            </article>
                          ))}
                          {favorites.donors.map(favorite => (
                            <article className="glass-card favorite-card" key={`donor:${favorite.key}`}>
                              <button type="button" className="favorite-card-open" onClick={() => openFavoriteDonor(favorite)}>
                                <span className="favorite-card-type">Observed donor{favorite.country ? ` · ${favorite.country}` : ""}</span>
                                <strong>{favorite.name}</strong>
                                <small>{favorite.funding} · {favorite.grantCount.toLocaleString("en-GB")} grants · {favorite.recipientCount.toLocaleString("en-GB")} recipients</small>
                              </button>
                              <button type="button" className="favorite-toggle is-favorite" aria-label={`Remove ${favorite.name} from favorites`} onClick={() => toggleFavoriteDonor(favorite)}><Star size={16} fill="currentColor" /></button>
                            </article>
                          ))}
                        </div>
                      </section>
                    )}

                    {favorites.donorRequests.length > 0 && (
                      <section className="favorites-section" aria-labelledby="favorite-donor-requests-heading">
                        <div className="favorites-section-heading">
                          <div><span className="page-eyebrow">Observed grant data</span><h3 id="favorite-donor-requests-heading">Saved donor requests</h3></div>
                          <span>{favorites.donorRequests.length}</span>
                        </div>
                        <div className="favorites-grid">
                          {favorites.donorRequests.map(favorite => (
                            <article className="glass-card favorite-card" key={favorite.key}>
                              <button type="button" className="favorite-card-open" onClick={() => openFavoriteDonorRequest(favorite)}>
                                <span className="favorite-card-type">Donor request</span>
                                <strong>{favorite.label}</strong>
                                <small>Reopen these observed funders and transactions</small>
                              </button>
                              <button type="button" className="favorite-toggle is-favorite" aria-label={`Remove ${favorite.label} from favorites`} onClick={() => toggleFavoriteDonorRequest(favorite)}><Star size={16} fill="currentColor" /></button>
                            </article>
                          ))}
                        </div>
                      </section>
                    )}

                    {favorites.grantExplorers.length > 0 && (
                      <section className="favorites-section" aria-labelledby="favorite-grant-explorers-heading">
                        <div className="favorites-section-heading">
                          <div><span className="page-eyebrow">Observed grant data</span><h3 id="favorite-grant-explorers-heading">Saved grant explorations</h3></div>
                          <span>{favorites.grantExplorers.length}</span>
                        </div>
                        <div className="favorites-grid">
                          {favorites.grantExplorers.map(favorite => (
                            <article className="glass-card favorite-card" key={favorite.key}>
                              <button type="button" className="favorite-card-open" onClick={() => openFavoriteGrantExplorer(favorite)}>
                                <span className="favorite-card-type">Observed Grant Explorer</span>
                                <strong>{favorite.label.replace(/^(Grant period|Programme area) · /, "")}</strong>
                                <small>Reopen this selected grant slice and its source evidence</small>
                              </button>
                              <button type="button" className="favorite-toggle is-favorite" aria-label={`Remove ${favorite.label} from favorites`} onClick={() => toggleFavoriteGrantExplorer(favorite)}><Star size={16} fill="currentColor" /></button>
                            </article>
                          ))}
                        </div>
                      </section>
                    )}

                    {favorites.researchViews.length > 0 && (
                      <section className="favorites-section" aria-labelledby="favorite-views-heading">
                        <div className="favorites-section-heading">
                          <div><span className="page-eyebrow">Saved views</span><h3 id="favorite-views-heading">Organization Research</h3></div>
                          <span>{favorites.researchViews.length}</span>
                        </div>
                        <div className="favorites-grid">
                          {favorites.researchViews.map(favorite => (
                            <article className="glass-card favorite-card" key={favorite.key}>
                              <button type="button" className="favorite-card-open" onClick={() => openFavoriteResearch(favorite)}>
                                <span className="favorite-card-type">Organization Research</span>
                                <strong>{favorite.label.replace("Organization Research · ", "")}</strong>
                                <small>Reopen saved filters and ranking</small>
                              </button>
                              <button type="button" className="favorite-toggle is-favorite" aria-label={`Remove ${favorite.label} from favorites`} onClick={() => removeFavoriteResearch(favorite.key)}><Star size={16} fill="currentColor" /></button>
                            </article>
                          ))}
                        </div>
                      </section>
                    )}
                  </div>
                )}
              </section>
            )
          )}

          {/* Retained for rollback only; the active Overview is the compact, globally-filtered dashboard above. */}
          {SHOW_LEGACY_OVERVIEW && activeTab === "overview" && (
            <div className="flex-col-gap overview-layout">
              {/* KPIs */}
              <div className="grid-cols-4 overview-kpi-grid">
                <div className="glass-card kpi-card">
                  <div className="kpi-icon"><Building2 size={24} /></div>
                  <div className="kpi-value-container">
                    <span className="kpi-label">Organizations Indexed</span>
                    <span className="kpi-value">{stats.total_charities}</span>
                  </div>
                </div>

                <div className="glass-card kpi-card">
                  <div className="kpi-icon accent-sunny"><DollarSign size={24} /></div>
                  <div className="kpi-value-container">
                    <span className="kpi-label">Average Annual Income</span>
                    <span className="kpi-value">{formatCurrency(stats.average_income)}</span>
                  </div>
                </div>

                <div className="glass-card kpi-card">
                  <div className="kpi-icon"><TrendingUp size={24} /></div>
                  <div className="kpi-value-container">
                    <span className="kpi-label">Average Expenditure</span>
                    <span className="kpi-value">{formatCurrency(stats.average_expenditure)}</span>
                  </div>
                </div>

                <div className="glass-card kpi-card">
                  <div className="kpi-icon accent-sunny"><Activity size={24} /></div>
                  <div className="kpi-value-container">
                    <span className="kpi-label">Grants Monitored</span>
                    <span className="kpi-value">{stats.total_grants ?? "Unavailable"}</span>
                  </div>
                </div>
              </div>

              <Suspense fallback={<section className="glass-card route-loading" role="status"><LoaderCircle className="spin" size={22} /> Loading world map…</section>}>
                <GrantWorldMap
                  data={mapData}
                  loading={mapLoading}
                  error={mapError}
                  filters={mapFilters}
                  onOpenOrganizationDirectory={openOrganizationDirectoryFromMap}
                />
              </Suspense>

              <div className="analytics-charts-grid">
                {/* Monthly source-derived grant awards */}
                <div className="glass-card analytics-chart-card">
                  <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", alignItems: "flex-start", flexWrap: "wrap" }}>
                    <div>
                      <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "4px" }}>Monthly Grant Awards</h3>
                      <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                        Derived from cached 360Giving records · award-date basis
                      </span>
                    </div>
                    {grantAnalyticsCurrencies.length > 0 && (
                      <label style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                        Currency
                        <select
                          value={grantAnalyticsCurrency}
                          onChange={(event) => {
                            setGrantAnalyticsCurrency(event.target.value);
                            fetchGrantAnalytics(undefined, event.target.value);
                            fetchMapData(undefined, event.target.value);
                          }}
                          style={{ marginLeft: "8px", padding: "5px 8px" }}
                        >
                          {grantAnalyticsCurrencies.map(currency => (
                            <option key={currency} value={currency}>{currency}</option>
                          ))}
                        </select>
                      </label>
                    )}
                  </div>
                  {grantAnalyticsLoading ? (
                    <div className="loading-container" style={{ minHeight: "300px" }}><div className="spinner" /></div>
                  ) : grantAnalyticsError ? (
                    <div className="data-notice data-notice-warning">{grantAnalyticsError}</div>
                  ) : grantTrends?.status === "currency_selection_required" ? (
                    <div className="data-notice data-notice-warning">Select one currency; grant amounts are never combined across currencies.</div>
                  ) : grantTrends?.status === "available" && grantTrends.items.length > 0 ? (
                    <>
                      <div className="analytics-chart-plot">
                        <Suspense fallback={<div className="loading-container" role="status"><div className="spinner" /> Loading chart…</div>}>
                          <GrantAwardsChart items={grantTrends.items} currency={grantTrends.currency || "GBP"} formatCurrency={formatCurrency} />
                        </Suspense>
                      </div>
                      <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                        Active currency: {grantTrends.currency} · Period {grantTrends.period?.from}–{grantTrends.period?.to}, anchored to the latest available award month. {unknownTrendMonths} month(s) have unknown source coverage.
                      </span>
                      {analyticsAreStale && (
                        <div className="data-notice data-notice-warning">Cached grant data was last refreshed more than 30 days ago.</div>
                      )}
                    </>
                  ) : (
                    <div className="data-notice data-notice-warning">
                      No qualifying monthly grant awards are available for the selected currency.
                    </div>
                  )}
                  <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                    Coverage reflects available source records and is not representative of the entire funding market.
                  </span>
                </div>

                {/* Programme allocation derived from stored source/inferred classifications */}
                <div className="glass-card analytics-chart-card">
                <div style={{ marginBottom: "16px" }}>
                  <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "4px" }}>Grant Allocation by Programme Area</h3>
                  <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                    Derived from cached 360Giving records · active currency: {grantThemes?.currency || grantAnalyticsCurrency || "unselected"}
                  </span>
                </div>
                {grantAnalyticsLoading ? (
                  <div className="loading-container" style={{ minHeight: "300px" }}><div className="spinner" /></div>
                ) : grantAnalyticsError ? (
                  <div className="data-notice data-notice-warning">{grantAnalyticsError}</div>
                ) : grantThemes?.status === "currency_selection_required" ? (
                  <div className="data-notice data-notice-warning">Select one currency before comparing programme allocations.</div>
                ) : grantThemes?.status === "available" && grantThemes.items.length > 0 ? (
                  <>
                    <div className="analytics-chart-plot">
                      <Suspense fallback={<div className="loading-container" role="status"><div className="spinner" /> Loading chart…</div>}>
                        <ProgrammeAllocationChart items={grantThemes.items} currency={grantThemes.currency || "GBP"} formatCurrency={formatCurrency} />
                      </Suspense>
                    </div>
                    <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginTop: "12px" }}>
                      Classified: {grantThemes.classification_coverage.classified_grant_count} / {grantThemes.classification_coverage.qualifying_grant_count} grants ({classificationPercentage}%). Unclassified remains visible. Multi-category amounts and counts are split equally to preserve totals.
                    </div>
                    {classificationPercentage < 50 ? (
                      <div className="data-notice data-notice-warning">Strong coverage warning: fewer than half of qualifying grants have an accepted programme classification.</div>
                    ) : classificationPercentage < 80 ? (
                      <div className="data-notice data-notice-warning">Moderate coverage warning: programme classification coverage is below the 80% presentation threshold.</div>
                    ) : null}
                    <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "8px" }}>
                      Allocation method: equal split across accepted categories · source categories precede inferred categories · inference threshold {grantThemes.inference_confidence_threshold}.
                    </div>
                  </>
                ) : (
                  <div className="data-notice data-notice-warning">No qualifying programme-area allocation is available for the selected currency.</div>
                )}
                <span style={{ display: "block", fontSize: "11px", color: "var(--text-muted)", marginTop: "12px" }}>
                  Coverage reflects available source records and is not representative of the entire funding market. Philea organization metadata is not included as grant flow.
                </span>
                </div>
              </div>
            </div>
          )}

          {/* Primary observed-donor directory. Organization and registry search
              remain available as clearly-labelled secondary research tools. */}
          {activeTab === "directory" && directoryMode === "donors" && (
            <Suspense fallback={<div className="route-loading"><LoaderCircle className="spin" size={22} /> Loading Donor Directory…</div>}>
              <DonorDirectoryPage
                apiBase={API_BASE}
                online={isBffOnline}
                selectedSources={selectedDataSources}
                onHeaderStateChange={setDonorHeaderState}
                onBackToLandscape={(scope: GrantScope) => {
                  const query = applyGrantScopeToParams(
                    new URLSearchParams(window.location.search),
                    { ...scope, beneficiaryCountry: undefined },
                    { includeCountry: false, persistEmptySources: true },
                  );
                  ["funder_country", "funder_sort", "funder_page", "donor_search", "donor_status", "donor"].forEach(key => query.delete(key));
                  query.delete("view");
                  const overviewSuffix = query.toString();
                  window.history.pushState({}, "", `${window.location.pathname}${overviewSuffix ? `?${overviewSuffix}` : ""}`);
                  setActiveTab("overview");
                  window.scrollTo({ top: 0, behavior: "smooth" });
                }}
                onOpenOrganizationResearch={() => {
                  navigateApplication("research");
                  window.scrollTo({ top: 0, behavior: "smooth" });
                }}
                onOpenRegistrySearch={() => {
                  navigateApplication("registry");
                  window.scrollTo({ top: 0, behavior: "smooth" });
                }}
                onOpenProfile={(profileId, profileName, sourceFunderKey, sourceFunderName) => openLinkedDirectoryProfile({
                  charity_id: profileId,
                  name: profileName,
                  sourceFunderKey,
                  sourceFunderName,
                })}
                favoriteDonorKeys={favorites.donors.map(donor => donor.key)}
                onToggleFavoriteDonor={toggleFavoriteDonor}
                favoriteDonorRequestKeys={favorites.donorRequests.map(request => request.key)}
                onToggleFavoriteDonorRequest={toggleFavoriteDonorRequest}
              />
            </Suspense>
          )}

          {activeTab === "directory" && directoryMode === "registry" && (
            <>
              <div className="page-introduction secondary-page-introduction">
                <div><span className="page-eyebrow">Secondary research</span><h2>Advanced Charity Commission Search</h2><p>Search official Charity Commission records and registered locations.</p></div>
                <button type="button" className="btn btn-secondary" onClick={() => navigateApplication("donors")}><ArrowLeft size={16} /> Donor Directory</button>
              </div>
              <Suspense fallback={<div className="route-loading"><LoaderCircle className="spin" size={22} /> Loading registry search…</div>}>
                <RegistryDirectory
                  key={directoryHandoff.version}
                  apiBase={API_BASE}
                  online={isBffOnline}
                  initialQuery={directoryHandoff.query}
                  initialBeneficiaryGeography=""
                  onOpenEnrichedProfile={(id, name) => {
                    setSelectedCharity({
                      registered_charity_number: id,
                      suffix: 0,
                      link: "",
                      charity_name: name,
                      reg_status: "UNKNOWN",
                      reporting_status: "",
                      removal_reason: null,
                      latest_income: null,
                      latest_expenditure: null,
                      programme_areas_source: [],
                      programme_areas_inferred: [],
                      geographic_focus_source: [],
                      geographic_focus_inferred: [],
                      organization_type: "unknown",
                      source_names: [],
                      transaction_coverage: "unknown",
                    });
                  }}
                />
              </Suspense>
            </>
          )}

          {activeTab === "directory" && directoryMode === "profiles" && (
            <>
              <div className="page-introduction secondary-page-introduction">
                <div><span className="page-eyebrow">Secondary research</span><h2>Organization Research</h2><p>Explore enriched organization profiles. Inclusion does not imply observed funding activity.</p></div>
                <div className="secondary-page-actions">
                  <button
                    type="button"
                    className={`favorite-toggle page-favorite-toggle${researchViewIsFavorite ? " is-favorite" : ""}`}
                    aria-label={`${researchViewIsFavorite ? "Remove" : "Save"} current Organization Research view ${researchViewIsFavorite ? "from" : "to"} favorites`}
                    aria-pressed={researchViewIsFavorite}
                    onClick={toggleCurrentResearchFavorite}
                  ><Star size={16} fill={researchViewIsFavorite ? "currentColor" : "none"} /></button>
                  <button type="button" className="btn btn-secondary" onClick={() => navigateApplication("donors")}><ArrowLeft size={16} /> Donor Directory</button>
                </div>
              </div>
            <div className="organization-primary-search">
              <div>
                <span className="organization-primary-search-label">Find an organization</span>
                <p>Search enriched organization profiles by name.</p>
              </div>
              <div className="organization-name-filter organization-primary-search-input">
                <Search size={16} aria-hidden="true" />
                <input
                  type="text"
                  className="form-input"
                  placeholder="Search organization name…"
                  value={searchTerm}
                  onChange={(event) => {
                    setSearchTerm(event.target.value);
                    setSuggestionsOpen(true);
                  }}
                  onFocus={() => setSuggestionsOpen(true)}
                  onBlur={() => window.setTimeout(() => setSuggestionsOpen(false), 120)}
                  aria-label="Search Organization Research by name"
                />
                {searchTerm && <button type="button" className="organization-name-filter-clear" onMouseDown={event => event.preventDefault()} onClick={() => setSearchTerm("")}>Clear</button>}
                {suggestionsOpen && searchTerm.trim().length >= 2 && (
                  <div className="organization-suggestions" role="listbox" aria-label="Organization suggestions">
                    {suggestionsLoading ? (
                      <span className="organization-suggestions-status">Looking for organizations…</span>
                    ) : organizationSuggestions.length > 0 ? (
                      organizationSuggestions.map(suggestion => (
                        <button
                          key={`${suggestion.registered_charity_number}-${suggestion.source_record_id || ""}`}
                          type="button"
                          role="option"
                          onMouseDown={(event) => event.preventDefault()}
                          onClick={() => {
                            setSearchTerm(suggestion.charity_name);
                            setSuggestionsOpen(false);
                            setSelectedCharity(suggestion);
                          }}
                        >
                          <strong>{suggestion.charity_name}</strong>
                          <span>{suggestion.primary_source || "Organization profile"}{suggestion.headquarters_country ? ` · ${suggestion.headquarters_country}` : ""}</span>
                        </button>
                      ))
                    ) : (
                      <span className="organization-suggestions-status">No matching organization profiles.</span>
                    )}
                  </div>
                )}
              </div>
            </div>
            <div className="organization-research-workspace">
              {profileFiltersOpen && <div className="filter-drawer-backdrop" onMouseDown={() => setProfileFiltersOpen(false)}>
                <aside className="filter-drawer organization-research-filter-drawer" role="dialog" aria-modal="true" aria-label="Organization research filters" onMouseDown={event => event.stopPropagation()}>
                  <div className="filter-drawer-header">
                    <div><span>Organization Research</span><h3>Filters</h3></div>
                    <button type="button" onClick={() => setProfileFiltersOpen(false)} aria-label="Close filters"><X size={18} /></button>
                  </div>
                  <div id="organization-research-filters" className="filter-drawer-body organization-filter-drawer-body">
                <div className="filter-group organization-filter-section">
                  <span className="filter-label">Thematic Sector</span>
                  <div className="organization-filter-checklist">
                    {SECTORS.map((sec) => (
                      <label key={sec.value} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "13px", cursor: "pointer", color: "var(--text-secondary)" }}>
                        <input
                          type="checkbox"
                          checked={selectedTags.includes(sec.value)}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setSelectedTags([...selectedTags, sec.value]);
                            } else {
                              setSelectedTags(selectedTags.filter(t => t !== sec.value));
                            }
                          }}
                          style={{ cursor: "pointer", accentColor: "var(--nl-unicorn)" }}
                        />
                        <span>{sec.label}</span>
                      </label>
                    ))}
                  </div>
                </div>

                <div className="filter-group organization-filter-section">
                  <span className="filter-label">Foundation Location</span>
                  <div className="organization-filter-checklist">
                    {HEADQUARTERS_LOCATIONS.map((reg) => (
                      <label key={reg} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "13px", cursor: "pointer", color: "var(--text-secondary)" }}>
                        <input
                          type="checkbox"
                          checked={selectedFoundationRegions.includes(reg)}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setSelectedFoundationRegions([...selectedFoundationRegions, reg]);
                            } else {
                              setSelectedFoundationRegions(selectedFoundationRegions.filter(r => r !== reg));
                            }
                          }}
                          style={{ cursor: "pointer", accentColor: "var(--nl-unicorn)" }}
                        />
                        <span>{reg}</span>
                      </label>
                    ))}
                  </div>
                </div>

                <div className="filter-group organization-filter-section">
                  <span className="filter-label">Beneficiary Geography</span>
                  <div className="organization-filter-checklist">
                    {Array.from(new Set([
                      ...BENEFICIARY_GEOGRAPHIES,
                      ...beneficiaryLocationOptions,
                      ...selectedRecipientRegions,
                    ])).sort((left, right) => left.localeCompare(right)).map((reg) => (
                      <label key={reg} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "13px", cursor: "pointer", color: "var(--text-secondary)" }}>
                        <input
                          type="checkbox"
                          checked={selectedRecipientRegions.includes(reg)}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setSelectedRecipientRegions([...selectedRecipientRegions, reg]);
                            } else {
                              setSelectedRecipientRegions(selectedRecipientRegions.filter(r => r !== reg));
                            }
                          }}
                          style={{ cursor: "pointer", accentColor: "var(--nl-unicorn)" }}
                        />
                        <span>{reg}</span>
                      </label>
                    ))}
                  </div>
                </div>

                <div className="filter-group organization-filter-section organization-amount-filter">
                  <div className="organization-amount-filter-header">
                    <div>
                      <span className="filter-label">Annual expenditure</span>
                      <small>Minimum {ANNUAL_GIVING_LABELS[annualGivingIndex]}</small>
                    </div>
                    <label className="organization-amount-filter-maximum">
                      <span>Maximum</span>
                      <span className="organization-amount-input"><i>£</i><input
                        type="number"
                        min="0"
                        inputMode="decimal"
                        placeholder="No limit"
                        aria-label="Maximum annual expenditure"
                        value={maxAnnualGivingInput}
                        onChange={event => setMaxAnnualGivingInput(event.target.value)}
                      /></span>
                    </label>
                  </div>
                  <input
                    className="organization-amount-slider"
                    type="range"
                    min="0"
                    max={ANNUAL_GIVING_STEPS.length - 1}
                    value={annualGivingIndex}
                    aria-label="Minimum annual expenditure"
                    onChange={(e) => setAnnualGivingIndex(parseInt(e.target.value))}
                  />
                  <span className="organization-amount-filter-hint">Use the slider for the lower limit; leave maximum empty to include all larger organizations.</span>
                </div>

                <div className="filter-group organization-filter-section organization-amount-filter">
                  <div className="organization-amount-filter-header">
                    <div>
                      <span className="filter-label">Average grant size</span>
                      <small>Minimum {AVG_GRANT_SIZE_LABELS[avgGrantSizeIndex]} · ECB-converted EUR</small>
                    </div>
                    <label className="organization-amount-filter-maximum">
                      <span>Maximum</span>
                      <span className="organization-amount-input"><i>€</i><input
                        type="number"
                        min="0"
                        inputMode="decimal"
                        placeholder="No limit"
                        aria-label="Maximum average grant size in EUR"
                        value={maxAvgGrantSizeInput}
                        onChange={event => setMaxAvgGrantSizeInput(event.target.value)}
                      /></span>
                    </label>
                  </div>
                  <input
                    className="organization-amount-slider"
                    type="range"
                    min="0"
                    max={AVG_GRANT_SIZE_STEPS.length - 1}
                    value={avgGrantSizeIndex}
                    aria-label="Minimum average grant size in EUR"
                    onChange={(e) => setAvgGrantSizeIndex(parseInt(e.target.value))}
                  />
                  <span className="organization-amount-filter-hint">Calculated only from grants with a valid official ECB EUR conversion.</span>
                </div>

                <button
                  className="btn btn-secondary organization-filter-reset"
                  onClick={resetDirectoryFilters}
                >
                  Reset Filters
                </button>
                  </div>
                </aside>
              </div>}

              {/* Grid listings of charities */}
              <div className="flex-col-gap">
                {loading && !directoryInitialLoaded ? (
                  <div style={{ textAlign: "center", padding: "40px", color: "var(--text-secondary)" }}>Loading organisation profiles…</div>
                ) : (
                  <>
                    {loading && <div style={{ color: "var(--text-muted)", fontSize: "12px" }}>Updating organisation profiles…</div>}
                    <div className="organization-research-toolbar">
                      <span className="organization-result-count">Ranked across all matching profiles</span>
                      <label className="organization-sort-control">
                        <span>Sort</span>
                        <select value={profileSort} onChange={event => setProfileSort(event.target.value as "score_desc" | "income_desc" | "name_asc")}>
                          <option value="score_desc">Highest score first</option>
                          <option value="income_desc">Latest income</option>
                          <option value="name_asc">Organization name</option>
                        </select>
                      </label>
                    </div>
                    <div className="charity-grid">
                      {charities.map(ch => {
                        const programmeAreas = Array.from(new Set([...(ch.programme_areas_source || []), ...(ch.programme_areas_inferred || [])]));
                        const profileContext = [ch.organization_type?.replaceAll("_", " "), ch.headquarters_country].filter(Boolean).join(" · ");
                        const scoreAvailable = ch.relevance_score !== null && ch.relevance_score !== undefined;
                        const financialMetric = ch.latest_income !== null && ch.latest_income !== undefined
                          ? { label: "Latest income", value: formatCurrency(ch.latest_income) }
                          : ch.latest_expenditure !== null && ch.latest_expenditure !== undefined
                            ? { label: "Latest expenditure", value: formatCurrency(ch.latest_expenditure) }
                            : { label: "Financial data", value: "Unavailable" };
                        const favoriteKey = profileFavoriteKey(ch);
                        const isFavorite = favorites.profiles.some(item => item.key === favoriteKey);
                        return <article
                          key={`${ch.registered_charity_number}-${ch.source_record_id || ""}`}
                          className="glass-card charity-card organization-result-card"
                        >
                          <button
                            type="button"
                            className="organization-result-open"
                            onClick={() => setSelectedCharity(ch)}
                            aria-label={`Open organization profile for ${ch.charity_name}`}
                          >
                            <div className="organization-card-topline">
                              <span className={`organization-fit-score${scoreAvailable ? "" : " unavailable"}`} title={scoreAvailable ? "Target-profile relevance score" : "No fit score is available for this profile"}>{scoreAvailable ? `Fit ${Math.round(ch.relevance_score!)}` : "Fit —"}</span>
                            </div>
                            <h3 className="charity-card-name">{ch.charity_name}</h3>
                            {profileContext && <p className="organization-card-context">{profileContext}</p>}
                            <p className={`organization-card-focus${programmeAreas.length ? "" : " unavailable"}`}>{programmeAreas.length ? <>{programmeAreas[0]}{programmeAreas.length > 1 && <span> · +{programmeAreas.length - 1} theme{programmeAreas.length > 2 ? "s" : ""}</span>}</> : "Focus not yet classified"}</p>
                            <div className="charity-card-meta">
                              <span className="organization-card-financial"><small>{financialMetric.label}</small><strong>{financialMetric.value}</strong></span>
                              <ArrowRight size={17} aria-hidden="true" />
                            </div>
                          </button>
                          <button
                            type="button"
                            className={`favorite-toggle organization-card-favorite${isFavorite ? " is-favorite" : ""}`}
                            aria-label={`${isFavorite ? "Remove" : "Add"} ${ch.charity_name} ${isFavorite ? "from" : "to"} favorites`}
                            aria-pressed={isFavorite}
                            onClick={() => toggleFavoriteProfile(ch)}
                          ><Star size={16} fill={isFavorite ? "currentColor" : "none"} /></button>
                        </article>;
                      })}
                      {charities.length === 0 && (
                        <div className="glass-card directory-empty-state">
                          <div className="directory-empty-icon"><Building2 size={22} /></div>
                          <h3>No linked organizations found</h3>
                          <p>
                            No Organization Directory profile matches this filter combination.
                            {selectedRecipientRegions.length > 0 && (
                              <> The map can still contain 360Giving grants for {selectedRecipientRegions.join(", ")} when their funder exists only as a source transaction and has no linked Directory profile.</>
                            )}
                          </p>
                          <button type="button" className="btn btn-secondary" onClick={resetDirectoryFilters}>
                            Reset directory filters
                          </button>
                        </div>
                      )}
                      </div>
                  </>
                )}
                {!loading && profilesHaveMore && (
                  <button
                    type="button"
                    className="btn btn-secondary directory-load-more"
                    onClick={() => fetchCharities(undefined, true)}
                    disabled={loadingMoreProfiles}
                  >
                    {loadingMoreProfiles ? "Loading organization profiles…" : "Load 50 more organization profiles"}
                  </button>
                )}
              </div>
            </div>
            </>
          )}

          {/* TAB 3: ADMIN */}
          {activeTab === "admin" && (
            <div className="flex-col-gap">
              <div className="grid-cols-2">
                {/* Trigger Buttons */}
                <div className="glass-card" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                  <h3 style={{ fontSize: "16px", fontWeight: "600" }}>Predefined Pipeline Controls</h3>
                  <p style={{ fontSize: "14px", color: "var(--text-secondary)" }}>
                    Safely trigger the execution of scraping, crawling, or database reload operations.
                    These predefined modes run asynchronously and log metrics to protect against rate limits.
                  </p>

                  <div style={{ display: "flex", flexDirection: "column", gap: "12px", marginTop: "16px" }}>
                    <div style={{ display: "flex", gap: "12px" }}>
                      <button
                        className="btn btn-primary"
                        style={{ flexGrow: "1" }}
                        disabled={isTriggering || pipelineStatus.status === "running"}
                        onClick={() => triggerPipeline("full_run")}
                      >
                        <Play size={16} />
                        Trigger Pipeline
                      </button>
                    </div>

                    <div style={{ display: "flex", flexDirection: "column", gap: "16px", marginTop: "12px", borderTop: "1px solid var(--border-glass)", paddingTop: "16px" }}>
                      
                      {/* Limit & Impressum Scraper */}
                      <div style={{ display: "flex", gap: "16px" }}>
                        <div className="filter-group" style={{ flex: 1 }}>
                          <span className="filter-label">Scraping Limit (Foundations)</span>
                          <input
                            type="number"
                            className="form-input"
                            value={pipelineLimit}
                            onChange={(e) => setPipelineLimit(Math.min(100, Math.max(1, parseInt(e.target.value) || 20)))}
                            min="1"
                            max="100"
                          />
                          <span style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "4px" }}>
                            Maximum limit is 100
                          </span>
                        </div>
                        
                        <div className="filter-group" style={{ flex: 1, justifyContent: "center" }}>
                          <label className="checkbox-label" style={{ marginTop: "20px" }}>
                            <input
                              type="checkbox"
                              className="checkbox-input"
                              checked={enableImpressum}
                              onChange={(e) => setEnableImpressum(e.target.checked)}
                            />
                            Run Impressum Scraper (Standard)
                          </label>
                        </div>
                      </div>

                      {/* Search term & Forced IDs */}
                      <div style={{ display: "flex", gap: "16px" }}>
                        <div className="filter-group" style={{ flex: 1 }}>
                          <span className="filter-label">Search Term / Name Search</span>
                          <input
                            type="text"
                            className="form-input"
                            placeholder="e.g. foundation"
                            value={pipelineSearch}
                            onChange={(e) => setPipelineSearch(e.target.value)}
                          />
                          <span style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "4px" }}>
                            Force-scrapes charities by name search
                          </span>
                        </div>

                        <div className="filter-group" style={{ flex: 1 }}>
                          <span className="filter-label">Specific Charity IDs (comma-separated)</span>
                          <input
                            type="text"
                            className="form-input"
                            placeholder="e.g. 219907, 283322"
                            value={pipelineIds}
                            onChange={(e) => setPipelineIds(e.target.value)}
                          />
                          <span style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "4px" }}>
                            Force-scrapes specific charity numbers
                          </span>
                        </div>
                      </div>

                      {/* Fresh run flag */}
                      <div style={{ display: "flex", gap: "16px" }}>
                        <div className="filter-group" style={{ flex: 1 }}>
                          <label className="checkbox-label">
                            <input
                              type="checkbox"
                              className="checkbox-input"
                              checked={pipelineFresh}
                              onChange={(e) => setPipelineFresh(e.target.checked)}
                            />
                            Fresh Run (From Scratch)
                          </label>
                        </div>
                      </div>

                    </div>
                  </div>
                </div>

                {/* Status Indicator Panel */}
                <div className="glass-card" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                  <h3 style={{ fontSize: "16px", fontWeight: "600" }}>Pipeline Execution Status</h3>

                  <div style={{ display: "flex", flexDirection: "column", gap: "12px", marginTop: "12px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: "var(--text-secondary)" }}>Pipeline State:</span>
                      <span className={`badge ${pipelineStatus.status === "running" ? "badge-warning" : pipelineStatus.status === "success" ? "badge-success" : "badge-tag"}`}>
                        {pipelineStatus.status}
                      </span>
                    </div>

                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: "var(--text-secondary)" }}>Last Run Source:</span>
                      <span style={{ fontWeight: "600" }}>{pipelineStatus.last_run_source || "None"}</span>
                    </div>

                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span style={{ color: "var(--text-secondary)" }}>Execution Started At:</span>
                      <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px" }}>{pipelineStatus.started_at ? new Date(pipelineStatus.started_at).toLocaleTimeString() : "N/A"}</span>
                    </div>

                    {pipelineStatus.error && (
                      <div style={{ padding: "10px", background: "rgba(239,68,68,0.1)", color: "var(--semantic-error)", borderRadius: "6px", fontSize: "12px" }}>
                        <strong>Error:</strong> {pipelineStatus.error}
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* Pipeline Workflow Visualization */}
              <div className="glass-card" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                <h3 style={{ fontSize: "16px", fontWeight: "600" }}>Pipeline Data Flow Architecture</h3>
                <p style={{ fontSize: "14px", color: "var(--text-secondary)" }}>
                  Understand how information flows from upstream APIs into the central consolidated database. Each step depends on the previous to yield rich, linked dashboards.
                </p>

                <div style={{ display: "flex", flexWrap: "wrap", gap: "24px", justifyContent: "space-between", alignItems: "center", marginTop: "12px", padding: "16px", backgroundColor: "rgba(0,0,0,0.02)", borderRadius: "8px" }}>

                  {/* Step 1 */}
                  <div style={{ flex: "1 1 200px", display: "flex", flexDirection: "column", gap: "8px", borderLeft: "3px solid var(--primary-color)", paddingLeft: "12px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <span style={{ backgroundColor: "var(--primary-color)", color: "white", borderRadius: "50%", width: "24px", height: "24px", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "12px", fontWeight: "bold" }}>1</span>
                      <h4 style={{ fontWeight: "600", fontSize: "14px" }}>Scrape Charities</h4>
                    </div>
                    <span style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
                      Queries the official Charity Commission Register for registrations, financials, and thematic structures.
                    </span>
                    <span style={{ fontSize: "11px", fontFamily: "monospace", color: "var(--primary-color)" }}>register_of_charities</span>
                  </div>

                  <ArrowRight size={20} style={{ color: "var(--text-muted)", alignSelf: "center" }} className="hide-mobile" />

                  {/* Step 2 */}
                  <div style={{ flex: "1 1 200px", display: "flex", flexDirection: "column", gap: "8px", borderLeft: "3px solid var(--accent-color)", paddingLeft: "12px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <span style={{ backgroundColor: "var(--accent-color)", color: "white", borderRadius: "50%", width: "24px", height: "24px", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "12px", fontWeight: "bold" }}>2</span>
                      <h4 style={{ fontWeight: "600", fontSize: "14px" }}>Scrape Grants</h4>
                    </div>
                    <span style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
                      Queries the 360Giving API to discover funding and recipient transactions matching active charity IDs.
                    </span>
                    <span style={{ fontSize: "11px", fontFamily: "monospace", color: "var(--accent-color)" }}>360giving</span>
                  </div>

                  <ArrowRight size={20} style={{ color: "var(--text-muted)", alignSelf: "center" }} className="hide-mobile" />

                  {/* Step 3 */}
                  <div style={{ flex: "1 1 200px", display: "flex", flexDirection: "column", gap: "8px", borderLeft: "3px solid #10b981", paddingLeft: "12px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <span style={{ backgroundColor: "#10b981", color: "white", borderRadius: "50%", width: "24px", height: "24px", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "12px", fontWeight: "bold" }}>3</span>
                      <h4 style={{ fontWeight: "600", fontSize: "14px" }}>Consolidate & Load</h4>
                    </div>
                    <span style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
                      Normalizes coordinates, runs the Impressum contact crawler, links funders to recipients, and loads database.
                    </span>
                    <span style={{ fontSize: "11px", fontFamily: "monospace", color: "#10b981" }}>consolidate</span>
                  </div>

                </div>
              </div>

              {/* Logs output window */}
              <div className="terminal-window">
                <div className="terminal-header">
                  <div className="terminal-title">
                    <Terminal size={14} />
                    <span>Pipeline Logging Output (Tailing data/pipeline_run.log)</span>
                  </div>
                  <div className="terminal-dots">
                    <span className="dot dot-red"></span>
                    <span className="dot dot-yellow"></span>
                    <span className="dot dot-green"></span>
                  </div>
                </div>
                <div className={`terminal-body ${pipelineStatus.status === "running" ? "running" : ""}`}>
                  {logs}
                  <div ref={logsEndRef}></div>
                </div>
              </div>
            </div>
          )}

        </div>
      </main>

      {/* Modal Profile / Detailed Charity View Overlay */}
      {selectedCharity && (
        <div style={{
          position: "fixed",
          inset: 0,
          backgroundColor: "rgba(15, 23, 42, 0.45)",
          backdropFilter: "blur(4px)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 100,
          padding: "24px"
        }}>
          <div ref={profileModalRef} tabIndex={-1} className="glass-card" role="dialog" aria-modal="true" aria-labelledby="profile-modal-title" style={{
            width: "100%",
            maxWidth: "960px",
            maxHeight: "90vh",
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: "24px",
            backgroundColor: "var(--bg-surface-opaque)",
            border: "1px solid var(--border-glass-focus)"
          }}>
            {/* Header */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", borderBottom: "1px solid var(--border-glass)", paddingBottom: "16px" }}>
              <div>
                <span className="charity-card-id">
                  {selectedCharity.primary_source === "Philea" ? `Philea #${selectedCharity.source_record_id}` : `#${selectedCharity.registered_charity_number}`}
                </span>
                <h2 id="profile-modal-title" style={{ fontSize: "22px", fontWeight: "700", marginTop: "4px" }}>{selectedCharity.charity_name}</h2>
                {isBffOnline && <div className="profile-fit-summary" aria-label="Target-profile relevance score">
                  <span>Profile fit</span>
                  <strong>{scoreData?.score === null ? "—" : scoreData ? `${Math.round(scoreData.score)}/100` : "Loading…"}</strong>
                  {scoreData && <small>{Math.round(scoreData.data_completeness * 100)}% evidence</small>}
                </div>}
              </div>
              <div className="profile-modal-actions">
                <button
                  type="button"
                  className={`favorite-toggle${favorites.profiles.some(item => item.key === profileFavoriteKey(selectedCharity)) ? " is-favorite" : ""}`}
                  aria-label={`${favorites.profiles.some(item => item.key === profileFavoriteKey(selectedCharity)) ? "Remove" : "Add"} ${selectedCharity.charity_name} ${favorites.profiles.some(item => item.key === profileFavoriteKey(selectedCharity)) ? "from" : "to"} favorites`}
                  aria-pressed={favorites.profiles.some(item => item.key === profileFavoriteKey(selectedCharity))}
                  onClick={() => toggleFavoriteProfile(selectedCharity)}
                ><Star size={16} fill={favorites.profiles.some(item => item.key === profileFavoriteKey(selectedCharity)) ? "currentColor" : "none"} /></button>
                <button
                  className="btn btn-secondary"
                  style={{ padding: "6px 12px" }}
                  aria-label="Close profile"
                  onClick={clearActiveProfileSafely}
                >
                  Close Profile
                </button>
              </div>
            </div>

            {profileIsLoading && (
              <div className="profile-background-loading" role="status" aria-live="polite">
                <LoaderCircle className="profile-background-loading-spinner" size={22} aria-hidden="true" />
                <div className="profile-background-loading-copy">
                  <strong>Loading profile data in the background</strong>
                  <span>Core profile, source history, grant activity, relationships, and relevance are requested in parallel.</span>
                </div>
                <ul className="profile-background-loading-tasks" aria-label="Profile data loading status">
                  <li className={profileLoading.detail.status === "loading" ? "is-loading" : "is-ready"}>Profile</li>
                  {selectedSourceFunderProfileKey && <li className={profileLoading.source_record.status === "loading" ? "is-loading" : "is-ready"}>Source history</li>}
                  <li className={profileLoading.grants.status === "loading" ? "is-loading" : "is-ready"}>Grants</li>
                  <li className={profileLoading.relationships.status === "loading" ? "is-loading" : "is-ready"}>Relationships</li>
                  <li className={profileLoading.score.status === "loading" ? "is-loading" : "is-ready"}>Fit</li>
                </ul>
              </div>
            )}

            {profileSectionErrors.length > 0 && (
              <div className="data-notice data-notice-error" role="status" aria-live="polite">
                {profileSectionErrors.map(([key, section]) => (
                  <div key={key}><strong>{key.replaceAll("_", " ")}:</strong> {section.error}</div>
                ))}
              </div>
            )}

            <section className="news-briefing-card" aria-labelledby="ai-news-briefing-title">
              <div className="news-briefing-heading">
                <div>
                  <span>AI research</span>
                  <h3 id="ai-news-briefing-title">News briefing</h3>
                  <p>Recent external coverage, summarized from cited source articles.{selectedNewsAliases.length ? ` Also searching linked names: ${selectedNewsAliases.join(", ")}.` : ""}</p>
                </div>
                <div className="news-briefing-actions">
                  {savedNewsRun && !newsLoading && (
                    <button type="button" className="btn btn-secondary" onClick={() => toggleSavedNewsRun(savedNewsRun)}>
                      {savedNewsRunIsOpen ? "Close saved briefing" : `Open saved · ${formatNewsDate(savedNewsRun.savedAt)}`}
                    </button>
                  )}
                  {newsSummary && !newsLoading && (
                    <button type="button" className="btn btn-secondary" onClick={() => downloadNewsBriefingPdf(newsSummary)}>
                      <Download size={16} /> Download PDF
                    </button>
                  )}
                  <button
                    type="button"
                    className={`btn ${newsLoading ? "btn-secondary" : "btn-primary"}`}
                    onClick={() => fetchFoundationNews(selectedCharity)}
                    disabled={newsLoading}
                  >
                    {newsLoading ? <><span className="spinner-mini" /> Researching…</> : <><Newspaper size={16} /> Research latest news</>}
                  </button>
                </div>
              </div>

              {savedNewsRun && !newsSummary && !newsLoading && (
                <div className="news-saved-hint">
                  <span>Saved briefing available</span>
                  <p>Generated {formatNewsDate(savedNewsRun.generated_at)} · {savedNewsRun.sources.length} cited source{savedNewsRun.sources.length === 1 ? "" : "s"} · stored in this browser.</p>
                </div>
              )}

              {newsLoading && (
                <div className="news-progress" role="status" aria-live="polite">
                  <div className="news-progress-heading"><LoaderCircle className="spin" size={18} /><span>Researching current coverage</span></div>
                  <ol>
                    {NEWS_PROGRESS_STEPS.map((step, index) => {
                      const activeIndex = newsProgressStep ? NEWS_PROGRESS_STEPS.findIndex(item => item.key === newsProgressStep) : 0;
                      const state = index < activeIndex ? "complete" : index === activeIndex ? "active" : "pending";
                      return <li key={step.key} className={state}>
                        <i aria-hidden="true">{state === "complete" ? "✓" : index + 1}</i>
                        <span><strong>{step.label}</strong><small>{step.detail}</small></span>
                      </li>;
                    })}
                  </ol>
                </div>
              )}

              {newsError && <div className="news-error" role="alert"><strong>News research could not finish.</strong><span>{newsError}</span></div>}

              {newsSummary && (
                <div className="news-briefing-result">
                  <div className="news-result-meta">
                    <span>{newsRunMode === "illustrative" ? "Illustrative local briefing" : "Live external research"}</span>
                    <span>Generated {formatNewsDate(newsSummary.generated_at)}</span>
                    <span>Coverage: last {newsSummary.searched_weeks} weeks</span>
                    <span>{visibleNewsSources.length} cited source{visibleNewsSources.length === 1 ? "" : "s"}</span>
                  </div>
                  <div className="news-summary-copy">{renderMarkdown(newsSummary.summary)}</div>
                  {visibleNewsSources.length > 0 && (
                    <div className="news-source-section">
                      <div><span>Evidence</span><h4>Articles used in this briefing</h4></div>
                      <div className="news-source-list">
                        {visibleNewsSources.map(source => (
                          <a key={`${source.link}:${source.title}`} href={source.link} target="_blank" rel="noopener noreferrer" className="news-source-item">
                            <span className="news-source-date">{formatNewsDate(source.published)}</span>
                            <strong>{source.title}</strong>
                            <small>{source.source}{source.note ? ` · ${source.note}` : ""}</small>
                          </a>
                        ))}
                      </div>
                    </div>
                  )}
                  <details className="news-methodology">
                    <summary>How this briefing was produced</summary>
                    <p>It searches Google News for the organization, resolves and reads the returned publisher articles where possible, then generates a summary grounded in the cited sources. It is evidence support, not a statement from the organization.</p>
                  </details>
                </div>
              )}
            </section>

            {/* Contact details & Address */}
            {profileLoading.detail.status === "loading" && !selectedCharityDetail && (
              <div className="profile-section-loading" role="status">
                <LoaderCircle size={18} aria-hidden="true" />
                <span>Loading core profile information…</span>
              </div>
            )}
            {selectedCharityDetail && selectedCharityDetail.all_details && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "16px", backgroundColor: "rgba(0,0,0,0.02)", padding: "16px", borderRadius: "8px", border: "1px solid var(--border-glass)" }}>
                <div>
                  <span style={{ fontSize: "12px", color: "var(--text-secondary)", fontWeight: "600", display: "block" }}>Website</span>
                  {selectedCharityDetail.all_details.web ? (
                    <a href={selectedCharityDetail.all_details.web.startsWith("http") ? selectedCharityDetail.all_details.web : `https://${selectedCharityDetail.all_details.web}`} target="_blank" rel="noopener noreferrer" style={{ color: "var(--nl-unicorn)", fontSize: "14px", textDecoration: "underline", wordBreak: "break-all" }}>
                      {selectedCharityDetail.all_details.web}
                    </a>
                  ) : (
                    <span style={{ fontSize: "14px", color: "var(--text-muted)" }}>Not available</span>
                  )}
                </div>
                <div>
                  <span style={{ fontSize: "12px", color: "var(--text-secondary)", fontWeight: "600", display: "block" }}>Email Address</span>
                  {selectedCharityDetail.all_details.email ? (
                    <a href={`mailto:${selectedCharityDetail.all_details.email}`} style={{ color: "var(--nl-unicorn)", fontSize: "14px", textDecoration: "underline", wordBreak: "break-all" }}>
                      {selectedCharityDetail.all_details.email}
                    </a>
                  ) : (
                    <span style={{ fontSize: "14px", color: "var(--text-muted)" }}>Not available</span>
                  )}
                </div>
                <div>
                  <span style={{ fontSize: "12px", color: "var(--text-secondary)", fontWeight: "600", display: "block" }}>Phone Number</span>
                  <span style={{ fontSize: "14px", color: "var(--text-primary)" }}>{selectedCharityDetail.all_details.phone || "Not available"}</span>
                </div>
                <div>
                  <span style={{ fontSize: "12px", color: "var(--text-secondary)", fontWeight: "600", display: "block" }}>Address</span>
                  <span style={{ fontSize: "14px", color: "var(--text-primary)", display: "block", lineHeight: "1.4" }}>
                    {[
                      selectedCharityDetail.all_details.address_line_one,
                      selectedCharityDetail.all_details.address_line_two,
                      selectedCharityDetail.all_details.address_line_three,
                      selectedCharityDetail.all_details.address_line_four,
                      selectedCharityDetail.all_details.address_line_five,
                      selectedCharityDetail.all_details.address_post_code
                    ].filter(Boolean).join(", ") || "Not available"}
                  </span>
                </div>
              </div>
            )}

            {selectedCharityDetail && isBffOnline && (
              <div className="glass-card" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "16px", backgroundColor: "rgba(255,255,255,0.02)" }}>
                <div>
                  <span className="kpi-label">Programme areas</span>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "8px" }}>
                    {(selectedCharityDetail.programme_areas_source || []).map((area: string) => (
                      <span className="status-badge" key={`source-${area}`}>{area} · source</span>
                    ))}
                    {(selectedCharityDetail.programme_areas_inferred || []).map((area: string) => (
                      <span className="status-badge" key={`inferred-${area}`}>{area} · rule-inferred</span>
                    ))}
                    {!(selectedCharityDetail.programme_areas_source || []).length && !(selectedCharityDetail.programme_areas_inferred || []).length && (
                      <span style={{ color: "var(--text-muted)", fontSize: "12px" }}>Unavailable</span>
                    )}
                  </div>
                </div>
                <div>
                  <span className="kpi-label">Geography</span>
                  <div style={{ marginTop: "8px", fontSize: "12px", color: "var(--text-secondary)" }}>
                    Headquarters: {selectedCharityDetail.headquarters_country || "Unavailable"}
                    {selectedCharityDetail.headquarters_region ? ` · ${selectedCharityDetail.headquarters_region}` : ""}
                  </div>
                  <div style={{ marginTop: "6px", fontSize: "12px", color: "var(--text-secondary)" }}>
                    Stated/inferred focus: {(selectedCharityDetail.geographic_focus_inferred || []).join(", ") || "Unavailable"}
                  </div>
                </div>
                <div>
                  <span className="kpi-label">Classification provenance</span>
                  <div style={{ marginTop: "8px", fontSize: "12px", color: "var(--text-secondary)" }}>
                    Deterministic rules · {selectedCharityDetail.enrichment_rule_version || "version unavailable"}
                  </div>
                  <div style={{ marginTop: "6px", fontSize: "12px", color: "var(--text-secondary)" }}>
                    Source: {(selectedCharityDetail.source_names || []).join(" · ") || "Unavailable"}
                  </div>
                  <div style={{ marginTop: "6px", fontSize: "12px", color: "var(--text-secondary)" }}>
                    Type: {selectedCharityDetail.organization_type || "unknown"} · Coverage: {selectedCharityDetail.transaction_coverage || "unknown"}
                  </div>
                  {(selectedCharityDetail.programme_area_review_required || selectedCharityDetail.geography_review_required) && (
                    <div className="status-badge status-warning" style={{ marginTop: "8px" }}>Low-confidence or ambiguous evidence · review required</div>
                  )}
                </div>
              </div>
            )}

            {isBffOnline && (
              <div className="glass-card" style={{ backgroundColor: "rgba(255,255,255,0.02)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", alignItems: "flex-start", marginBottom: "14px" }}>
                  <div>
                    <h3 style={{ fontSize: "15px", fontWeight: "600", marginBottom: "4px" }}>Target-profile relevance</h3>
                    <div style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                      Example profile · deterministic decision support · not a prediction
                    </div>
                  </div>
                  <span className="status-badge profile-score-status">Experimental score</span>
                </div>
                {profileLoading.score.status === "loading" ? (
                  <div className="profile-section-loading" role="status">
                    <LoaderCircle size={18} aria-hidden="true" />
                    <span>Calculating target-profile relevance…</span>
                  </div>
                ) : scoreData ? (
                  <>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: "12px", marginBottom: "16px" }}>
                      <div><span className="kpi-label">Relevance</span><div className="kpi-value" style={{ fontSize: "22px" }}>{scoreData.score === null ? "Unavailable" : `${scoreData.score.toFixed(1)}/100`}</div></div>
                      <div><span className="kpi-label">Completeness</span><div className="kpi-value" style={{ fontSize: "22px" }}>{Math.round(scoreData.data_completeness * 100)}%</div></div>
                    </div>
                    <details className="profile-score-breakdown">
                      <summary>Why this score?</summary>
                      <div style={{ display: "grid", gap: "8px", marginTop: "12px" }}>
                        {Object.entries(scoreData.components).map(([name, component]) => {
                          const historicalEvidence = name === "historical_grant_size_fit" ? component.evidence[0] : null;
                          const observedAverage = historicalEvidence?.observed_average_grant;
                          const targetAverage = historicalEvidence?.target_average_grant;
                          const evidenceCurrency = historicalEvidence?.currency;
                          const hasGrantComparison = (
                            typeof observedAverage === "number"
                            && typeof targetAverage === "number"
                            && typeof evidenceCurrency === "string"
                          );
                          return (
                            <div key={name} style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: "12px", fontSize: "12px", padding: "8px 10px", border: "1px solid var(--border-glass)", borderRadius: "6px" }}>
                              <span style={{ color: "var(--text-secondary)" }}>{name.replaceAll("_", " ")} · weight {Math.round(component.weight * 100)}%</span>
                              <span style={{ fontWeight: 600, color: component.available ? "var(--text-primary)" : "var(--text-muted)", textAlign: "right" }}>
                                {component.available ? `${component.score?.toFixed(1)}/100` : component.missing_reason || "Unavailable"}
                                {hasGrantComparison && <small style={{ display: "block", marginTop: "2px", color: "var(--text-muted)", fontWeight: 500 }}>
                                  {formatCurrency(observedAverage, evidenceCurrency)} observed vs {formatCurrency(targetAverage, evidenceCurrency)} target
                                </small>}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                      <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "12px" }}>
                        Version {scoreData.score_version}. The score is the weighted sum of all criteria; missing criteria contribute zero and are reflected in completeness.
                      </div>
                    </details>
                  </>
                ) : (
                  <div style={{ color: "var(--text-muted)", fontSize: "12px" }}>Experimental score unavailable.</div>
                )}
              </div>
            )}

            {/* One organisation needs a ranked relationship view, not a giant many-to-one Sankey. */}
            <section className="grant-relationship-card" aria-labelledby="observed-relationships-title">
              <div className="grant-relationship-heading">
                <div>
                  <span>Observed grant data</span>
                  <h3 id="observed-relationships-title">Funding relationships</h3>
                  <p>Confirmed 360Giving transactions linked to this organisation.</p>
                </div>
                {sankeyData?.currency && <strong>{sankeyData.currency === "EUR" ? "EUR · ECB converted" : `${sankeyData.currency} · original amounts`}</strong>}
              </div>
              {profileLoading.relationships.status === "loading" ? (
                <div className="profile-section-loading" role="status">
                  <LoaderCircle size={18} aria-hidden="true" />
                  <span>Loading observed funding relationships…</span>
                </div>
              ) : selectedCharityFlowRows.length ? (
                <div className="grant-relationship-layout">
                  <div className="grant-relationship-list">
                    {selectedCharityFlowRows.map(row => (
                      <article key={`${row.direction}-${row.counterparty}`}>
                        <div className="grant-relationship-row-heading">
                          <div><span>{row.direction}</span><strong title={row.counterparty}>{row.counterparty}</strong></div>
                          <b>{formatCurrency(row.amount, sankeyData?.currency || "EUR")}</b>
                        </div>
                        <div className="grant-relationship-bar" aria-hidden="true"><i style={{ width: `${Math.max(5, (row.amount / selectedCharityFlowMaximum) * 100)}%` }} /></div>
                        <small>{row.grantCount.toLocaleString("en-GB")} observed {row.grantCount === 1 ? "grant" : "grants"}</small>
                      </article>
                    ))}
                  </div>
                  <aside className="grant-relationship-subject">
                    <span>{selectedCharityFlowRows.every(row => row.direction === "Received from") ? "Funding received by" : selectedCharityFlowRows.every(row => row.direction === "Awarded to") ? "Funding awarded by" : "Observed organisation"}</span>
                    <strong>{selectedCharity.charity_name}</strong>
                    <b>{formatCurrency(selectedCharityFlowTotal, sankeyData?.currency || "EUR")}</b>
                    <small>{selectedCharityFlowRows.length} observed funding relationships</small>
                  </aside>
                </div>
              ) : (
                <div className="data-notice data-notice-warning" style={{ padding: "20px", textAlign: "center" }}>
                  {sankeyData?.status === "organization_level_only"
                      ? "This source provides organization-level intelligence only; transaction-level grant coverage is unavailable."
                    : sankeyData?.status === "request_failed"
                      ? "Grant-flow data could not be loaded."
                      : "No confirmed link to observed 360Giving transactions is available for this Directory profile. This is not evidence that the organisation has made no grants."}
                </div>
              )}
              {(sankeyData?.excludedCount || 0) > 0 && <p className="grant-relationship-footnote">{sankeyData?.excludedCount} records excluded because they could not be compared in the selected currency.</p>}
            </section>

            {/* Financial Balance Summary */}
            <div className="grid-cols-2">
              <div className="glass-card" style={{ display: "flex", alignItems: "center", gap: "16px", backgroundColor: "rgba(255,255,255,0.02)" }}>
                <div className="kpi-icon"><TrendingUp size={20} /></div>
                <div className="kpi-value-container">
                  <span className="kpi-label">Annual Income</span>
                  <span className="kpi-value" style={{ fontSize: "20px" }}>{formatCurrency(selectedCharity.latest_income)}</span>
                </div>
              </div>

              <div className="glass-card" style={{ display: "flex", alignItems: "center", gap: "16px", backgroundColor: "rgba(255,255,255,0.02)" }}>
                <div className="kpi-icon accent-sunny"><TrendingDown size={20} /></div>
                <div className="kpi-value-container">
                  <span className="kpi-label">Annual Expenses</span>
                  <span className="kpi-value" style={{ fontSize: "20px" }}>{formatCurrency(selectedCharity.latest_expenditure)}</span>
                </div>
              </div>
            </div>

            {/* Foundation Trend Chart */}
            {selectedCharityDetail && selectedCharityDetail.financial_history && selectedCharityDetail.financial_history.length > 0 && (
              <div>
                <h3 style={{ fontSize: "15px", fontWeight: "600", marginBottom: "12px", color: "var(--text-secondary)" }}>Financial History Trends</h3>
                <div style={{ width: "100%", height: "200px", padding: "10px", backgroundColor: "var(--nl-ash-light)", borderRadius: "var(--radius-md)", border: "1px solid var(--border-glass)" }}>
                  <Suspense fallback={<div className="loading-container" role="status"><div className="spinner" /> Loading chart…</div>}>
                    <FinancialHistoryChart data={financialHistoryData} formatCurrency={formatCurrency} />
                  </Suspense>
                </div>
              </div>
            )}

            {/* Individual Grants Transaction Table */}
            <div>
              <h3 style={{ fontSize: "15px", fontWeight: "600", marginBottom: "4px", color: "var(--text-secondary)" }}>Observed Grant Transactions</h3>
              <div style={{ fontSize: "11px", color: "var(--text-muted)", marginBottom: "12px" }}>Cached 360Giving records · EUR values use the historical ECB average for the award month; original source amounts are retained.</div>
              <div className="table-container" style={{ maxHeight: "250px", overflowY: "auto" }}>
                <table className="custom-table">
                  <thead>
                    <tr>
                      <th>Grant ID</th>
                      <th>Funder / Recipient Name</th>
                      <th>Awarded amount</th>
                      <th>Description</th>
                      <th>Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {profileLoading.grants.status === "loading" ? (
                      <tr>
                        <td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)" }}>
                          <span className="profile-table-loading"><LoaderCircle size={16} aria-hidden="true" /> Loading observed grant transactions…</span>
                        </td>
                      </tr>
                    ) : charityGrants.map(gr => (
                      <tr key={gr.grant_id}>
                        <td style={{ fontFamily: "var(--font-mono)", fontSize: "12px" }}>{gr.grant_id}</td>
                        <td>{gr.funding_charity_id === selectedCharity.registered_charity_number ? gr.recipient_name : (gr.funding_name || "Unknown funder")}</td>
                        <td style={{ fontWeight: "600", color: "var(--nl-unicorn)" }} title={gr.exchange_rate_date ? `ECB reference rate date: ${gr.exchange_rate_date}` : undefined}>
                          {gr.amount_eur !== null && gr.amount_eur !== undefined ? <>
                            {formatCurrency(gr.amount_eur, "EUR")}
                            {gr.currency !== "EUR" && <small style={{ display: "block", fontWeight: "500", color: "var(--text-muted)", marginTop: "2px" }}>({formatCurrency(gr.amount, gr.currency)})</small>}
                          </> : formatCurrency(gr.amount, gr.currency)}
                        </td>
                        <td style={{ fontSize: "12px", color: "var(--text-secondary)" }}>{gr.description}</td>
                        <td style={{ whiteSpace: "nowrap" }}>{gr.date}</td>
                      </tr>
                    ))}
                    {profileLoading.grants.status !== "loading" && charityGrants.length === 0 && (
                      <tr>
                        <td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)" }}>
                          {grantStatus === "transaction_data_unavailable"
                            ? "Transaction data is unavailable in the current data source."
                            : grantStatus === "organization_level_only"
                              ? "Philea provides organization-level intelligence only; no transaction records are assigned."
                            : "No matching observed grant transactions were found. Absence is not proof that no grants exist."}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

          </div>
        </div>
      )}

    </div>
  );
}
