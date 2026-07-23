import { useState, useEffect, useRef } from "react";
import {
  Building2,
  TrendingUp,
  Activity,
  LogOut,
  Search,
  SlidersHorizontal,
  Terminal,
  ArrowRight,
  TrendingDown,
  DollarSign,
  Database,
  Play,
  Newspaper
} from "lucide-react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  AreaChart,
  Area,
  Sankey
} from "recharts";
import GrantWorldMap from "./components/GrantWorldMap";
import amplifyLogo from "./assets/amplify-logo.svg";
import type {
  GrantMapFilterOptions,
  GrantMapFilters,
  GrantMapResponse,
} from "./components/GrantWorldMap";

// Configuration for API requests
const API_BASE = import.meta.env.VITE_API_BASE_URL
  || `${window.location.protocol}//${window.location.hostname}:8000`;
const DEMO_USERNAME = import.meta.env.VITE_BFF_USERNAME || "admin";
const DEMO_PASSWORD = import.meta.env.VITE_BFF_PASSWORD || "password";

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
  currency: string;
  description: string;
  date: string;
  recipient_region: string;
  tags: string[];
}

interface SankeyNode {
  name: string;
}

interface SankeyLink {
  source: number;
  target: number;
  value: number;
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
const AVG_GRANT_SIZE_LABELS = ["£0", "£1k", "£5k", "£10k", "£50k", "£100k", "£250k", "£500k", "£1M"];

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

const GRANT_MAP_FILTER_OPTIONS: GrantMapFilterOptions = {
  sectors: SECTORS,
  headquartersLocations: HEADQUARTERS_LOCATIONS,
  beneficiaryGeographies: BENEFICIARY_GEOGRAPHIES,
  annualGivingSteps: ANNUAL_GIVING_STEPS,
  annualGivingLabels: ANNUAL_GIVING_LABELS,
  averageGrantSteps: AVG_GRANT_SIZE_STEPS,
  averageGrantLabels: AVG_GRANT_SIZE_LABELS,
};

export default function App() {
  const [activeTab, setActiveTab] = useState<"overview" | "directory" | "admin">("overview");

  // Data states
  const [stats, setStats] = useState<KPIStats>(MOCK_STATS);
  const [charities, setCharities] = useState<Charity[]>(MOCK_CHARITIES);
  const [mapData, setMapData] = useState<GrantMapResponse>(EMPTY_MAP);
  const [mapLoading, setMapLoading] = useState(false);
  const [mapError, setMapError] = useState<string | null>(null);
  const [mapFilters, setMapFilters] = useState<GrantMapFilters>(EMPTY_GRANT_MAP_FILTERS);
  const [grantTrends, setGrantTrends] = useState<GrantTrendsResponse | null>(null);
  const [grantThemes, setGrantThemes] = useState<GrantThemesResponse | null>(null);
  const [grantAnalyticsLoading, setGrantAnalyticsLoading] = useState(false);
  const [grantAnalyticsError, setGrantAnalyticsError] = useState<string | null>(null);
  const [grantAnalyticsCurrency, setGrantAnalyticsCurrency] = useState("");
  const [selectedCharity, setSelectedCharity] = useState<Charity | null>(null);
  const [selectedCharityDetail, setSelectedCharityDetail] = useState<any>(null);
  const [charityGrants, setCharityGrants] = useState<GrantDetail[]>([]);
  const [grantStatus, setGrantStatus] = useState("data_unavailable");
  const [sankeyData, setSankeyData] = useState<SankeyData | null>(null);
  const [scoreData, setScoreData] = useState<ScoreResponse | null>(null);

  // News summarizer states
  const [newsLoading, setNewsLoading] = useState(false);
  const [newsSummary, setNewsSummary] = useState<any | null>(null);
  const [newsError, setNewsError] = useState<string | null>(null);

  // Filter states
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [selectedFoundationRegions, setSelectedFoundationRegions] = useState<string[]>([]);
  const [selectedRecipientRegions, setSelectedRecipientRegions] = useState<string[]>([]);
  const [annualGivingIndex, setAnnualGivingIndex] = useState<number>(0);
  const [avgGrantSizeIndex, setAvgGrantSizeIndex] = useState<number>(0);
  const [loading, setLoading] = useState(false);
  const [isBffOnline, setIsBffOnline] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [apiError, setApiError] = useState<string | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);


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


  // Fetch initial configuration on mount
  useEffect(() => {
    checkBffHealth();
  }, []);

  useEffect(() => {
    fetchStats();
    fetchCharities();
  }, [selectedTags, selectedFoundationRegions, selectedRecipientRegions, annualGivingIndex, avgGrantSizeIndex]);

  useEffect(() => {
    const debounce = window.setTimeout(() => {
      fetchCharities();
    }, 350);
    return () => window.clearTimeout(debounce);
  }, [searchTerm]);

  useEffect(() => {
    if (selectedCharity) {
      setSelectedCharityDetail(null);
      setNewsSummary(null);
      setNewsError(null);
      setNewsLoading(false);
      fetchCharityDetail(selectedCharity.registered_charity_number);
      fetchCharityGrants(selectedCharity.registered_charity_number);
      fetchSankeyData(selectedCharity.registered_charity_number);
      fetchScoreData(selectedCharity.registered_charity_number);
    } else {
      setSelectedCharityDetail(null);
      setNewsSummary(null);
      setNewsError(null);
      setNewsLoading(false);
      setScoreData(null);
    }
  }, [selectedCharity]);

  useEffect(() => {
    if (activeTab === "admin") {
      fetchPipelineStatus();
      const interval = setInterval(fetchPipelineStatus, 3000);
      return () => clearInterval(interval);
    }
  }, [activeTab]);

  useEffect(() => {
    if (pipelineStatus.status === "success") {
      fetchStats();
      fetchCharities();
      fetchMapData();
      fetchGrantAnalytics();
    }
  }, [pipelineStatus.status]);

  useEffect(() => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs]);

  const autoLogin = async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: DEMO_USERNAME, password: DEMO_PASSWORD }),
        credentials: "include"
      });
      if (resp.ok) {
        setAuthError(null);
        setIsBffOnline(true);
        console.log("Logged in to BFF successfully.");
        return true;
      }
      setAuthError("The backend is reachable, but automatic demo authentication failed.");
    } catch (e) {
      console.error("Auto login failed", e);
      setAuthError("The backend is reachable, but automatic demo authentication failed.");
    }
    return false;
  };

  const checkBffHealth = async () => {
    setInitialLoading(true);
    try {
      const resp = await fetch(`${API_BASE}/health`);
      if (resp.ok) {
        console.log("BFF Backend is online. Authenticating...");
        const loggedIn = await autoLogin();
        if (loggedIn) {
          setIsBffOnline(true);
          setApiError(null);
          // Load initial live dataset
          await Promise.all([
            fetchStats(true),
            fetchCharities(true),
            fetchMapData(true),
            fetchGrantAnalytics(true)
          ]);
          setInitialLoading(false);
          return;
        }
      }
      setIsBffOnline(false);
      setApiError("Backend unavailable. Values marked as illustrative are local prototype data.");
      console.warn("BFF offline. Falling back to mock dataset.");
    } catch {
      setIsBffOnline(false);
      setApiError("Backend unavailable. Values marked as illustrative are local prototype data.");
      console.warn("BFF offline. Falling back to mock dataset.");
    } finally {
      setInitialLoading(false);
    }
  };

  const fetchStats = async (forceOnline?: boolean) => {
    const isOnline = forceOnline !== undefined ? forceOnline : isBffOnline;
    if (!isOnline) return;
    try {
      const resp = await fetch(`${API_BASE}/api/charities/stats`, { credentials: "include" });
      if (resp.ok) {
        const data = await resp.json();
        setStats(data);
      } else {
        setApiError(`Statistics request failed (${resp.status}).`);
      }
    } catch (e) {
      console.error("Failed to fetch stats", e);
      setApiError("Statistics are temporarily unavailable.");
    }
  };

  const fetchCharities = async (forceOnline?: boolean) => {
    const isOnline = forceOnline !== undefined ? forceOnline : isBffOnline;
    setLoading(true);
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
      const minAvgGrant = AVG_GRANT_SIZE_STEPS[avgGrantSizeIndex];
      if (minAvgGrant > 0) {
        filtered = filtered.filter(c => ((c.latest_expenditure || 0) / 10) >= minAvgGrant);
      }
      setCharities(filtered);
      setLoading(false);
      return;
    }
    try {
      let url = `${API_BASE}/api/charities?limit=50`;
      if (searchTerm) url += `&search=${encodeURIComponent(searchTerm)}`;
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
      const minAvgGrant = AVG_GRANT_SIZE_STEPS[avgGrantSizeIndex];
      if (minAvgGrant > 0) {
        url += `&min_avg_grant_size=${minAvgGrant}`;
      }

      const resp = await fetch(url, { credentials: "include" });
      if (resp.ok) {
        const data = await resp.json();
        setCharities(data);
        setApiError(null);
      } else {
        setApiError(`Directory request failed (${resp.status}).`);
      }
    } catch (e) {
      console.error("Failed to fetch charities", e);
      setApiError("The organization directory is temporarily unavailable.");
    } finally {
      setLoading(false);
    }
  };

  const fetchCharityDetail = async (id: number) => {
    if (!isBffOnline) {
      // Mock fallback
      const mock = MOCK_CHARITIES.find(c => c.registered_charity_number === id);
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
          { financial_period_end_date: "2024-12-31", income: mock.latest_income, expenditure: mock.latest_expenditure },
          { financial_period_end_date: "2023-12-31", income: (mock.latest_income || 0) * 0.95, expenditure: (mock.latest_expenditure || 0) * 0.92 },
          { financial_period_end_date: "2022-12-31", income: (mock.latest_income || 0) * 0.90, expenditure: (mock.latest_expenditure || 0) * 0.88 }
        ]
      } : null);
      return;
    }
    try {
      const resp = await fetch(`${API_BASE}/api/charities/${id}`, { credentials: "include" });
      if (resp.ok) {
        const data = await resp.json();
        setSelectedCharityDetail(data);
        setApiError(null);
      } else {
        setSelectedCharityDetail(null);
        setApiError(`Organization detail request failed (${resp.status}).`);
      }
    } catch (e) {
      console.error("Failed to fetch charity details", e);
      setSelectedCharityDetail(null);
      setApiError("Organization details are temporarily unavailable.");
    }
  };

  const fetchFoundationNews = async (name: string) => {
    setNewsLoading(true);
    setNewsError(null);
    setNewsSummary(null);

    if (!isBffOnline) {
      // Mock news fallback
      setTimeout(() => {
        setNewsSummary({
          foundation: name,
          summary: `Here is a mock summary of recent news for "${name}". The foundation has been actively expanding its socio-economic support programs in the UK. They announced a new partnership with local food banks to address food insecurity. Furthermore, they are investing in digital transformation initiatives to streamline grant-making processes for small charities.`,
          sources: [
            { title: "Netlight News: Insecurity Partnership", link: "https://example.com/news1", source: "Netlight Post", published: "Mon, 20 Jul 2026 10:00:00 GMT" },
            { title: "Charity Digital: Streamlining Grants", link: "https://example.com/news2", source: "Charity Daily", published: "Sun, 19 Jul 2026 14:30:00 GMT" }
          ]
        });
        setNewsLoading(false);
      }, 1500);
      return;
    }

    try {
      const resp = await fetch(`${API_BASE}/api/news/${encodeURIComponent(name)}/summary`, { credentials: "include" });
      if (resp.ok) {
        const data = await resp.json();
        setNewsSummary(data);
      } else {
        const errorDetail = await resp.json();
        setNewsError(errorDetail.detail || "Failed to load news summary from backend.");
      }
    } catch (e: any) {
      console.error("Failed to fetch news summary", e);
      setNewsError("An error occurred while connecting to the news service.");
    } finally {
      setNewsLoading(false);
    }
  };

  const fetchMapData = async (
    forceOnline?: boolean,
    currencyOverride?: string,
    filtersOverride?: GrantMapFilters,
  ) => {
    const isOnline = forceOnline !== undefined ? forceOnline : isBffOnline;
    if (!isOnline) {
      setMapData(EMPTY_MAP);
      setMapLoading(false);
      return;
    }
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
        { credentials: "include" },
      );
      if (resp.ok) {
        const data: GrantMapResponse = await resp.json();
        setMapData(data);
      } else {
        setMapError(`Map-data request failed (${resp.status}).`);
      }
    } catch (e) {
      console.error("Failed to fetch map metrics", e);
      setMapData(EMPTY_MAP);
      setMapError("Map data is temporarily unavailable.");
    } finally {
      setMapLoading(false);
    }
  };

  const openOrganizationDirectoryFromMap = (filters: GrantMapFilters) => {
    setSearchTerm(filters.search);
    setSelectedTags(filters.tags);
    setSelectedFoundationRegions(filters.foundationRegions);
    setSelectedRecipientRegions(filters.fundingRegions);
    setAnnualGivingIndex(Math.max(0, ANNUAL_GIVING_STEPS.indexOf(filters.minAnnualGiving)));
    setAvgGrantSizeIndex(Math.max(0, AVG_GRANT_SIZE_STEPS.indexOf(filters.minAvgGrantSize)));
    setSelectedCharity(null);
    setActiveTab("directory");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const resetDirectoryFilters = () => {
    setSearchTerm("");
    setSelectedTags([]);
    setSelectedFoundationRegions([]);
    setSelectedRecipientRegions([]);
    setAnnualGivingIndex(0);
    setAvgGrantSizeIndex(0);
  };

  const fetchGrantAnalytics = async (forceOnline?: boolean, currencyOverride?: string) => {
    const isOnline = forceOnline !== undefined ? forceOnline : isBffOnline;
    if (!isOnline) {
      setGrantTrends(null);
      setGrantThemes(null);
      setGrantAnalyticsError("Grant analytics require the cached SQLite transaction database.");
      return;
    }
    setGrantAnalyticsLoading(true);
    setGrantAnalyticsError(null);
    const requestedCurrency = currencyOverride || grantAnalyticsCurrency;
    const currencyQuery = requestedCurrency
      ? `&currency=${encodeURIComponent(requestedCurrency)}`
      : "";
    try {
      const [trendsResponse, themesResponse] = await Promise.all([
        fetch(`${API_BASE}/api/charities/grants/trends?months=24${currencyQuery}`, {
          credentials: "include"
        }),
        fetch(`${API_BASE}/api/charities/grants/themes?${currencyQuery.slice(1)}`, {
          credentials: "include"
        })
      ]);
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
      console.error("Failed to fetch grant analytics", error);
      setGrantAnalyticsError("Grant analytics are temporarily unavailable.");
      setGrantTrends(null);
      setGrantThemes(null);
    } finally {
      setGrantAnalyticsLoading(false);
    }
  };

  const fetchCharityGrants = async (id: number) => {
    if (!isBffOnline) {
      setCharityGrants([]);
      setGrantStatus("transaction_data_unavailable");
      return;
    }
    try {
      const resp = await fetch(`${API_BASE}/api/charities/${id}/grants`, { credentials: "include" });
      if (resp.ok) {
        const data = await resp.json();
        setCharityGrants(data.grants || []);
        setGrantStatus(data.status || "data_unavailable");
      } else {
        setCharityGrants([]);
        setGrantStatus("request_failed");
        setApiError(`Grant transaction request failed (${resp.status}).`);
      }
    } catch (e) {
      console.error("Failed to fetch grants", e);
      setCharityGrants([]);
      setGrantStatus("request_failed");
      setApiError("Grant transactions are temporarily unavailable.");
    }
  };

  const fetchSankeyData = async (id: number) => {
    if (!isBffOnline) {
      setSankeyData({
        status: "transaction_data_unavailable",
        nodes: [],
        links: [],
        currency: null,
        excludedCount: 0,
      });
      return;
    }
    try {
      const resp = await fetch(`${API_BASE}/api/charities/${id}/sankey`, { credentials: "include" });
      if (resp.ok) {
        const data = await resp.json();
        // Parse names directly to index integers for Recharts Sankey
        const nodeMap = new Map();
        data.nodes.forEach((n: any, idx: number) => nodeMap.set(n.id, idx));

        const formattedLinks = data.links.map((l: any) => ({
          source: nodeMap.get(l.source),
          target: nodeMap.get(l.target),
          value: l.value
        }));

        setSankeyData({
          status: data.status,
          nodes: data.nodes.map((n: any) => ({ name: n.label })),
          links: formattedLinks,
          currency: data.metadata?.selected_currency || null,
          excludedCount: data.metadata?.excluded_grant_count || 0,
        });
      } else {
        setSankeyData({ status: "request_failed", nodes: [], links: [], currency: null, excludedCount: 0 });
        setApiError(`Sankey request failed (${resp.status}).`);
      }
    } catch (e) {
      console.error("Failed to fetch sankey metrics", e);
      setSankeyData({ status: "request_failed", nodes: [], links: [], currency: null, excludedCount: 0 });
      setApiError("Grant-flow data is temporarily unavailable.");
    }
  };

  const fetchScoreData = async (id: number) => {
    if (!isBffOnline) {
      setScoreData(null);
      return;
    }
    try {
      const resp = await fetch(`${API_BASE}/api/charities/${id}/score`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({}),
      });
      if (resp.ok) {
        setScoreData(await resp.json());
      } else {
        setScoreData(null);
        setApiError(`Experimental score request failed (${resp.status}).`);
      }
    } catch (e) {
      console.error("Failed to fetch experimental relevance score", e);
      setScoreData(null);
      setApiError("The experimental relevance score is temporarily unavailable.");
    }
  };

  const fetchPipelineStatus = async () => {
    if (!isBffOnline) return;
    try {
      const resp = await fetch(`${API_BASE}/api/admin/pipeline/status`, { credentials: "include" });
      if (resp.ok) {
        const data = await resp.json();
        setPipelineStatus(data);
      }

      const logResp = await fetch(`${API_BASE}/api/admin/pipeline/logs`, { credentials: "include" });
      if (logResp.ok) {
        const logData = await logResp.json();
        setLogs(logData.logs || "System idle.\n");
      }
    } catch (e) {
      console.error("Failed to get pipeline metrics", e);
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
        headers: { "Content-Type": "application/json" },
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
      console.error("Failed to trigger pipeline run", e);
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

  if (initialLoading) {
    return (
      <div style={{
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        alignItems: "center",
        height: "100vh",
        width: "100vw",
        background: "var(--bg-main, #0f111a)",
        color: "var(--text-main, #ffffff)",
        fontFamily: "'Inter', sans-serif",
        gap: "24px"
      }}>
        {/* Modern glowing spinner */}
        <div style={{
          position: "relative",
          width: "64px",
          height: "64px",
        }}>
          <div style={{
            boxSizing: "border-box",
            display: "block",
            position: "absolute",
            width: "64px",
            height: "64px",
            border: "6px solid var(--accent-sunny, #ffbb00)",
            borderRadius: "50%",
            animation: "spin 1.2s cubic-bezier(0.5, 0, 0.5, 1) infinite",
            borderColor: "var(--accent-sunny, #ffbb00) transparent transparent transparent"
          }} />
          <div style={{
            boxSizing: "border-box",
            display: "block",
            position: "absolute",
            width: "64px",
            height: "64px",
            border: "6px solid rgba(255, 187, 0, 0.1)",
            borderRadius: "50%"
          }} />
        </div>
        
        {/* Loading text */}
        <div style={{
          fontSize: "18px",
          fontWeight: "600",
          letterSpacing: "0.5px",
          color: "var(--text-main, #ffffff)",
          animation: "pulse 2s infinite"
        }}>
          Connecting to Foundation Intelligence...
        </div>
        
        {/* Style injection for spin & pulse keyframes */}
        <style>{`
          @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
          }
          @keyframes pulse {
            0% { opacity: 0.6; }
            50% { opacity: 1; }
            100% { opacity: 0.6; }
          }
        `}</style>
      </div>
    );
  }

  return (
    <div className="app-container">
      {/* Sidebar Navigation */}
      <aside className="sidebar">
        <div>
          <div className="sidebar-logo">
            <img src={amplifyLogo} alt="Amplify" />
            {!isBffOnline && <span style={{ fontSize: "10px", color: "var(--nl-sunny)", background: "var(--nl-sunny-glow)", padding: "2px 6px", borderRadius: "4px" }}>Offline</span>}
          </div>

          <nav className="sidebar-nav">
            <button
              className={`nav-item ${activeTab === "overview" ? "active" : ""}`}
              onClick={() => setActiveTab("overview")}
            >
              <TrendingUp size={18} />
              <span>Overview</span>
            </button>
            <button
              className={`nav-item ${activeTab === "directory" ? "active" : ""}`}
              onClick={() => setActiveTab("directory")}
            >
              <Building2 size={18} />
              <span>Organization Directory</span>
            </button>
            <button
              className={`nav-item ${activeTab === "admin" ? "active" : ""}`}
              onClick={() => setActiveTab("admin")}
            >
              <Terminal size={18} />
              <span>Pipeline Monitor</span>
            </button>
          </nav>
        </div>

        <div className="sidebar-footer">
          <div className="user-profile">
            <div className="user-avatar">NL</div>
            <div className="user-info">
              <span className="user-name">Netlight Guest</span>
              <span className="user-role">Administrator</span>
            </div>
          </div>
          <button className="nav-item" style={{ color: "var(--semantic-error)" }} onClick={() => checkBffHealth()}>
            <LogOut size={16} />
            <span>Check BFF Inflow</span>
          </button>
        </div>
      </aside>

      {/* Main Container Window */}
      <main className="main-content">
        <header className="header-bar">
          <h1 className="header-title">
            {activeTab === "overview" && "Foundation Intelligence Platform"}
            {activeTab === "directory" && "Foundation & Organization Directory"}
            {activeTab === "admin" && "Administrative Pipeline Monitor"}
          </h1>
          {isBffOnline && (
            <details className="data-sources-disclosure">
              <summary>
                <Database size={15} aria-hidden="true" />
                <span>Data sources</span>
              </summary>
              <div className="data-sources-panel">
                <strong>Cached source data</strong>
                <span>Used for the current dashboard view</span>
                <ul>
                  {(stats.source || ["Charity Commission", "360Giving"]).map(source => (
                    <li key={source}>{source}</li>
                  ))}
                </ul>
              </div>
            </details>
          )}
        </header>

        {/* Dynamic Pages */}
        <div className="page-container">
          {(authError || apiError) && (
            <div className="data-notice data-notice-error" role="status">
              {authError || apiError}
            </div>
          )}
          {!isBffOnline && (
            <div className="data-notice data-notice-warning" role="status">
              Illustrative prototype mode — displayed values are local examples, not live source data.
            </div>
          )}
          {/* TAB 1: OVERVIEW */}
          {activeTab === "overview" && (
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

              <GrantWorldMap
                data={mapData}
                loading={mapLoading}
                error={mapError}
                filters={mapFilters}
                filterOptions={GRANT_MAP_FILTER_OPTIONS}
                onFiltersChange={nextFilters => {
                  setMapFilters(nextFilters);
                  fetchMapData(undefined, undefined, nextFilters);
                }}
                onOpenOrganizationDirectory={openOrganizationDirectoryFromMap}
              />

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
                        <ResponsiveContainer width="100%" height="100%">
                          <AreaChart data={grantTrends.items}>
                            <defs>
                              <linearGradient id="colorGrantAwards" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="var(--nl-unicorn)" stopOpacity={0.3} />
                                <stop offset="95%" stopColor="var(--nl-unicorn)" stopOpacity={0} />
                              </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" />
                            <XAxis dataKey="month" stroke="var(--text-muted)" fontSize={10} tickFormatter={(month) => String(month).slice(2)} />
                            <YAxis stroke="var(--text-muted)" fontSize={11} tickFormatter={(value) => formatCurrency(Number(value), grantTrends.currency || "GBP")} />
                            <Tooltip
                              filterNull={false}
                              content={({ active, payload, label }: any) => {
                                if (!active || !payload?.length) return null;
                                const item = payload[0]?.payload as GrantTrendItem;
                                return (
                                  <div style={{ background: "var(--bg-surface-opaque)", border: "1px solid var(--border-glass)", padding: "10px", borderRadius: "8px" }}>
                                    <strong>{label}</strong>
                                    {item.coverage_status === "unknown" ? (
                                      <div>No source coverage established; not a confirmed zero.</div>
                                    ) : item.coverage_status === "partial" ? (
                                      <div>Source records exist, but no valid amount can be aggregated.</div>
                                    ) : (
                                      <>
                                        <div>{formatCurrency(item.total_amount, grantTrends.currency || "GBP")}</div>
                                        <div>{item.grant_count} recorded grants</div>
                                      </>
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
                            />
                          </AreaChart>
                        </ResponsiveContainer>
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
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={grantThemes.items} layout="vertical" margin={{ left: 25, right: 30 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" />
                          <XAxis type="number" stroke="var(--text-muted)" fontSize={11} tickFormatter={(value) => formatCurrency(Number(value), grantThemes.currency || "GBP")} />
                          <YAxis type="category" width={210} dataKey="programme_area" stroke="var(--text-muted)" fontSize={11} />
                          <Tooltip
                            formatter={(value) => formatCurrency(Number(value), grantThemes.currency || "GBP")}
                            labelFormatter={(label) => String(label)}
                            contentStyle={{ backgroundColor: "var(--bg-surface-opaque)", borderColor: "var(--border-glass)" }}
                          />
                          <Bar dataKey="allocated_amount" name="Allocated source amount" fill="var(--nl-unicorn)" radius={[0, 4, 4, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
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

          {/* TAB 2: DIRECTORY */}
          {activeTab === "directory" && (
            <div className="grid-cols-1-2" style={{ gridTemplateColumns: "280px 1fr" }}>
              {/* Sidebar Filters */}
              <div className="glass-card filter-group" style={{ height: "fit-content" }}>
                <h3 style={{ fontSize: "15px", fontWeight: "600", borderBottom: "1px solid var(--border-glass)", paddingBottom: "10px", display: "flex", alignItems: "center", gap: "8px" }}>
                  <SlidersHorizontal size={16} />
                  Filters
                </h3>

                <div className="filter-group" style={{ marginTop: "12px" }}>
                  <span className="filter-label">Search Name</span>
                  <div style={{ position: "relative" }}>
                    <Search size={14} style={{ position: "absolute", left: "10px", top: "12px", color: "var(--text-muted)" }} />
                    <input
                      type="text"
                      className="form-input"
                      placeholder="Organization name..."
                      style={{ paddingLeft: "32px" }}
                      value={searchTerm}
                      onChange={(e) => setSearchTerm(e.target.value)}
                    />
                  </div>
                </div>

                <div className="filter-group" style={{ marginTop: "12px" }}>
                  <span className="filter-label">Thematic Sector</span>
                  <div style={{
                    maxHeight: "150px",
                    overflowY: "auto",
                    border: "1px solid var(--border-glass)",
                    borderRadius: "6px",
                    padding: "8px",
                    backgroundColor: "rgba(0, 0, 0, 0.15)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "6px"
                  }}>
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

                <div className="filter-group" style={{ marginTop: "12px" }}>
                  <span className="filter-label">Foundation Location</span>
                  <div style={{
                    maxHeight: "130px",
                    overflowY: "auto",
                    border: "1px solid var(--border-glass)",
                    borderRadius: "6px",
                    padding: "8px",
                    backgroundColor: "rgba(0, 0, 0, 0.15)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "6px"
                  }}>
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

                <div className="filter-group" style={{ marginTop: "12px" }}>
                  <span className="filter-label">Beneficiary Geography</span>
                  <div style={{
                    maxHeight: "130px",
                    overflowY: "auto",
                    border: "1px solid var(--border-glass)",
                    borderRadius: "6px",
                    padding: "8px",
                    backgroundColor: "rgba(0, 0, 0, 0.15)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "6px"
                  }}>
                    {Array.from(new Set([
                      ...BENEFICIARY_GEOGRAPHIES,
                      ...selectedRecipientRegions,
                    ])).map((reg) => (
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

                <div className="filter-group" style={{ marginTop: "12px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span className="filter-label" style={{ margin: 0 }}>Min Annual Giving</span>
                    <span style={{ fontSize: "12px", fontWeight: "600", color: "var(--nl-unicorn)" }}>
                      {ANNUAL_GIVING_LABELS[annualGivingIndex]}
                    </span>
                  </div>
                  <input
                    type="range"
                    min="0"
                    max={ANNUAL_GIVING_STEPS.length - 1}
                    value={annualGivingIndex}
                    onChange={(e) => setAnnualGivingIndex(parseInt(e.target.value))}
                    style={{
                      width: "100%",
                      accentColor: "var(--nl-unicorn)",
                      cursor: "pointer",
                      marginTop: "4px"
                    }}
                  />
                </div>

                <div className="filter-group" style={{ marginTop: "12px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span className="filter-label" style={{ margin: 0 }}>Min Avg Grant Size</span>
                    <span style={{ fontSize: "12px", fontWeight: "600", color: "var(--nl-unicorn)" }}>
                      {AVG_GRANT_SIZE_LABELS[avgGrantSizeIndex]}
                    </span>
                  </div>
                  <input
                    type="range"
                    min="0"
                    max={AVG_GRANT_SIZE_STEPS.length - 1}
                    value={avgGrantSizeIndex}
                    onChange={(e) => setAvgGrantSizeIndex(parseInt(e.target.value))}
                    style={{
                      width: "100%",
                      accentColor: "var(--nl-unicorn)",
                      cursor: "pointer",
                      marginTop: "4px"
                    }}
                  />
                </div>

                <button
                  className="btn btn-secondary"
                  style={{ marginTop: "16px" }}
                  onClick={resetDirectoryFilters}
                >
                  Reset Filters
                </button>
              </div>

              {/* Grid listings of charities */}
              <div className="flex-col-gap">
                {loading ? (
                  <div style={{ textAlign: "center", padding: "40px" }}>Loading data from SQL...</div>
                ) : (
                  <div className="charity-grid">
                    {charities.map((ch, idx) => (
                      <div
                        key={idx}
                        className="glass-card charity-card"
                        onClick={() => setSelectedCharity(ch)}
                      >
                        <div className="charity-card-header">
                          <span className="charity-card-id">
                            {ch.primary_source === "Philea" ? `Philea #${ch.source_record_id}` : `#${ch.registered_charity_number}`}
                          </span>
                          <h3 className="charity-card-name">{ch.charity_name}</h3>
                        </div>
                        {isBffOnline && (
                          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginBottom: "10px" }}>
                            {ch.primary_source && <span className="status-badge">Source · {ch.primary_source}</span>}
                            {ch.organization_type && <span className="status-badge">Type · {ch.organization_type}</span>}
                            {ch.headquarters_country && <span className="status-badge">HQ · {ch.headquarters_country}</span>}
                            {[...(ch.programme_areas_source || []), ...(ch.programme_areas_inferred || [])].slice(0, 2).map((area) => (
                              <span className="status-badge" key={area}>{area}</span>
                            ))}
                            {(ch.programme_area_review_required || ch.geography_review_required) && (
                              <span className="status-badge status-warning">Review suggested</span>
                            )}
                            {ch.transaction_coverage === "organization_level_only" && (
                              <span className="status-badge status-warning">Organization-level data only</span>
                            )}
                          </div>
                        )}
                        <div className="charity-card-meta">
                          <div style={{ display: "flex", flexDirection: "column" }}>
                            <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>LATEST INCOME</span>
                            <span style={{ fontSize: "13px", fontWeight: "600" }}>{formatCurrency(ch.latest_income)}</span>
                          </div>
                          <span className="charity-card-amount">Select Details <ArrowRight size={14} style={{ display: "inline", marginLeft: "4px" }} /></span>
                        </div>
                      </div>
                    ))}
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
                )}
              </div>
            </div>
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
          <div className="glass-card" style={{
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
                <h2 style={{ fontSize: "22px", fontWeight: "700", marginTop: "4px" }}>{selectedCharity.charity_name}</h2>
              </div>
              <button
                className="btn btn-secondary"
                style={{ padding: "6px 12px" }}
                onClick={() => { setSelectedCharity(null); setSankeyData(null); }}
              >
                Close Profile
              </button>
            </div>

            {/* Contact details & Address */}
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
                  <span className="status-badge status-warning">Experimental score</span>
                </div>
                {scoreData ? (
                  <>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: "12px", marginBottom: "16px" }}>
                      <div><span className="kpi-label">Relevance</span><div className="kpi-value" style={{ fontSize: "22px" }}>{scoreData.score === null ? "Unavailable" : `${scoreData.score.toFixed(1)}/100`}</div></div>
                      <div><span className="kpi-label">Confidence</span><div className="kpi-value" style={{ fontSize: "22px" }}>{Math.round(scoreData.confidence * 100)}%</div></div>
                      <div><span className="kpi-label">Completeness</span><div className="kpi-value" style={{ fontSize: "22px" }}>{Math.round(scoreData.data_completeness * 100)}%</div></div>
                    </div>
                    <div style={{ display: "grid", gap: "8px" }}>
                      {Object.entries(scoreData.components).map(([name, component]) => (
                        <div key={name} style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: "12px", fontSize: "12px", padding: "8px 10px", border: "1px solid var(--border-glass)", borderRadius: "6px" }}>
                          <span style={{ color: "var(--text-secondary)" }}>{name.replaceAll("_", " ")} · weight {Math.round(component.weight * 100)}%</span>
                          <span style={{ fontWeight: 600, color: component.available ? "var(--text-primary)" : "var(--text-muted)" }}>
                            {component.available ? `${component.score?.toFixed(1)}/100` : component.missing_reason || "Unavailable"}
                          </span>
                        </div>
                      ))}
                    </div>
                    <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "12px" }}>
                      Version {scoreData.score_version}. Missing components are disclosed and excluded from relevance arithmetic; they reduce completeness and confidence.
                    </div>
                  </>
                ) : (
                  <div style={{ color: "var(--text-muted)", fontSize: "12px" }}>Experimental score unavailable.</div>
                )}
              </div>
            )}

            {/* Real grant-relationship Sankey */}
            <div>
              <h3 style={{ fontSize: "15px", fontWeight: "600", marginBottom: "4px", color: "var(--text-secondary)" }}>Observed Grant Relationships</h3>
              <div style={{ fontSize: "11px", color: "var(--text-muted)", marginBottom: "12px" }}>
                Derived from stored 360Giving donor → recipient transactions{(sankeyData?.currency) ? ` · ${sankeyData.currency}` : ""}
                {(sankeyData?.excludedCount || 0) > 0 ? ` · ${sankeyData?.excludedCount} records excluded` : ""}
              </div>
              {sankeyData && sankeyData.nodes.length > 0 ? (
                <div style={{ width: "100%", height: "300px", padding: "10px", backgroundColor: "var(--nl-ash-light)", borderRadius: "var(--radius-md)", border: "1px solid var(--border-glass)" }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <Sankey
                      data={sankeyData}
                      nodePadding={40}
                      nodeWidth={16}
                      margin={{ left: 130, right: 130, top: 15, bottom: 15 }}
                      link={{ stroke: "rgba(124, 58, 237, 0.15)" }}
                      node={(props) => {
                        const { x, y, width, height, name } = props;
                        const isRightNode = x > 300;
                        return (
                          <g>
                            <rect
                              x={x}
                              y={y}
                              width={width}
                              height={height}
                              fill="var(--nl-unicorn)"
                              rx={3}
                              ry={3}
                            />
                            <text
                              x={isRightNode ? x + width + 8 : x - 8}
                              y={y + height / 2 + 4}
                              textAnchor={isRightNode ? "start" : "end"}
                              fontSize={10}
                              fontWeight="600"
                              fill="var(--text-primary)"
                            >
                              {name}
                            </text>
                          </g>
                        );
                      }}
                    >
                      <Tooltip contentStyle={{ backgroundColor: "var(--bg-surface-opaque)", borderColor: "var(--border-glass)" }} />
                    </Sankey>
                  </ResponsiveContainer>
                </div>
              ) : (
                <div className="data-notice data-notice-warning" style={{ padding: "20px", textAlign: "center" }}>
                  {sankeyData?.status === "mixed_currency_requires_filter"
                    ? "Grant flows span multiple currencies; no amounts are combined without a currency filter."
                    : sankeyData?.status === "organization_level_only"
                      ? "This source provides organization-level intelligence only; transaction-level grant coverage is unavailable."
                    : sankeyData?.status === "request_failed"
                      ? "Grant-flow data could not be loaded."
                      : "No observed grant transactions are available for this organization."}
                </div>
              )}
            </div>

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
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart
                      data={[...selectedCharityDetail.financial_history].sort((a: any, b: any) =>
                        new Date(a.financial_period_end_date || "").getTime() - new Date(b.financial_period_end_date || "").getTime()
                      ).map((h: any) => ({
                        year: h.financial_period_end_date ? new Date(h.financial_period_end_date).getFullYear().toString() : "N/A",
                        Income: h.income || 0,
                        Expenditure: h.expenditure || 0
                      }))}
                      margin={{ top: 10, right: 10, left: 10, bottom: 0 }}
                    >
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
                      <YAxis fontSize={11} tickLine={false} tickFormatter={(v) => formatCurrency(v).replace("€", "")} />
                      <Tooltip formatter={(value) => formatCurrency(Number(value))} contentStyle={{ backgroundColor: "var(--bg-surface-opaque)", borderColor: "var(--border-glass)" }} />
                      <Area type="monotone" dataKey="Income" stroke="var(--nl-unicorn)" fillOpacity={1} fill="url(#colorInc)" strokeWidth={2} />
                      <Area type="monotone" dataKey="Expenditure" stroke="var(--nl-sunny)" fillOpacity={1} fill="url(#colorExp)" strokeWidth={2} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}

            {/* Recent News Summarizer Section */}
            <div style={{ borderTop: "1px solid var(--border-glass)", paddingTop: "20px", display: "flex", flexDirection: "column", gap: "12px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <h3 style={{ fontSize: "15px", fontWeight: "600", color: "var(--text-secondary)", margin: 0 }}>AI News Research & Summaries</h3>
                <button
                  className={`btn ${newsLoading ? "btn-secondary" : "btn-primary"}`}
                  onClick={() => fetchFoundationNews(selectedCharity.charity_name)}
                  disabled={newsLoading}
                  style={{ display: "flex", alignItems: "center", gap: "8px", padding: "6px 16px", fontSize: "13px" }}
                >
                  {newsLoading ? (
                    <>
                      <span className="spinner-mini"></span>
                      Researching News...
                    </>
                  ) : (
                    <>
                      <Newspaper size={16} />
                      Fetch Latest News Summary
                    </>
                  )}
                </button>
              </div>

              {newsLoading && (
                <div style={{ padding: "24px", textAlign: "center", color: "var(--text-secondary)", backgroundColor: "var(--nl-ash-light)", borderRadius: "var(--radius-md)", border: "1px solid var(--border-glass)", display: "flex", flexDirection: "column", alignItems: "center", gap: "12px" }}>
                  <div className="spinner"></div>
                  <span style={{ fontSize: "13px", fontWeight: "500" }}>Scraping RSS feeds, downloading news articles, and generating Claude summary...</span>
                </div>
              )}

              {newsError && (
                <div style={{ padding: "16px", color: "#ef4444", backgroundColor: "rgba(239, 68, 68, 0.08)", borderRadius: "var(--radius-md)", border: "1px solid rgba(239, 68, 68, 0.2)", fontSize: "13px" }}>
                  <strong>News Error:</strong> {newsError}
                </div>
              )}

              {newsSummary && (
                <div style={{ display: "flex", flexDirection: "column", gap: "16px", padding: "20px", backgroundColor: "var(--nl-ash-light)", borderRadius: "var(--radius-md)", border: "1px solid var(--border-glass)" }}>
                  <div style={{ color: "var(--text-primary)" }}>
                    {renderMarkdown(newsSummary.summary)}
                  </div>
                  {newsSummary.sources && newsSummary.sources.length > 0 && (
                    <div style={{ borderTop: "1px solid var(--border-glass)", paddingTop: "12px" }}>
                      <span style={{ fontSize: "11px", fontWeight: "600", color: "var(--text-muted)", textTransform: "uppercase", display: "block", marginBottom: "8px" }}>Sources Cited:</span>
                      <ul style={{ margin: 0, paddingLeft: "16px", display: "flex", flexDirection: "column", gap: "6px" }}>
                        {newsSummary.sources.map((src: any, idx: number) => (
                          <li key={idx} style={{ fontSize: "12px" }}>
                            <a href={src.link} target="_blank" rel="noopener noreferrer" style={{ color: "var(--nl-unicorn)", textDecoration: "underline" }}>
                              {src.title}
                            </a>
                            <span style={{ color: "var(--text-secondary)", marginLeft: "6px" }}>
                              ({src.source} • {new Date(src.published).toLocaleDateString()})
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Individual Grants Transaction Table */}
            <div>
              <h3 style={{ fontSize: "15px", fontWeight: "600", marginBottom: "4px", color: "var(--text-secondary)" }}>Observed Grant Transactions</h3>
              <div style={{ fontSize: "11px", color: "var(--text-muted)", marginBottom: "12px" }}>Cached 360Giving records · source amounts are not converted</div>
              <div className="table-container" style={{ maxHeight: "250px", overflowY: "auto" }}>
                <table className="custom-table">
                  <thead>
                    <tr>
                      <th>Grant ID</th>
                      <th>Funder / Recipient Name</th>
                      <th>Source amount</th>
                      <th>Description</th>
                      <th>Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {charityGrants.map((gr, idx) => (
                      <tr key={idx}>
                        <td style={{ fontFamily: "var(--font-mono)", fontSize: "12px" }}>{gr.grant_id}</td>
                        <td>{gr.funding_charity_id === selectedCharity.registered_charity_number ? gr.recipient_name : (gr.funding_name || "Unknown funder")}</td>
                        <td style={{ fontWeight: "600", color: "var(--nl-unicorn)" }}>{formatCurrency(gr.amount, gr.currency)}</td>
                        <td style={{ fontSize: "12px", color: "var(--text-secondary)" }}>{gr.description}</td>
                        <td style={{ whiteSpace: "nowrap" }}>{gr.date}</td>
                      </tr>
                    ))}
                    {charityGrants.length === 0 && (
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
