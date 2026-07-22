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

// Configuration for API requests
const API_BASE = "http://localhost:8000";

// Interface definitions
interface Charity {
  registered_charity_number: number;
  suffix: number;
  link: string;
  charity_name: string;
  reg_status: string;
  reporting_status: string;
  removal_reason: string | null;
  latest_income: number;
  latest_expenditure: number;
}

interface KPIStats {
  total_charities: number;
  active_charities: number;
  removed_charities: number;
  average_income: number;
  average_expenditure: number;
}

interface GrantMapItem {
  region: string;
  total_amount_eur: number;
  grants_count: number;
}

interface GrantDetail {
  grant_id: string;
  funding_charity_id: number | null;
  recipient_name: string;
  recipient_charity_id: number | null;
  amount_eur: number;
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
  nodes: SankeyNode[];
  links: SankeyLink[];
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

const MOCK_MAP: GrantMapItem[] = [
  { region: "London", total_amount_eur: 120500000, grants_count: 512 },
  { region: "North West", total_amount_eur: 42100000, grants_count: 188 },
  { region: "South East", total_amount_eur: 65800000, grants_count: 244 },
  { region: "Scotland", total_amount_eur: 31000000, grants_count: 98 },
  { region: "Wales", total_amount_eur: 5200000, grants_count: 42 }
];

const MOCK_THEMATIC_DATA = [
  { name: "Poverty relief", amount: 145 },
  { name: "Health & Cancer", amount: 210 },
  { name: "Youth Development", amount: 95 },
  { name: "Environment", amount: 120 },
  { name: "Humanitarian", amount: 180 }
];

const MOCK_TRENDS_DATA = [
  { month: "Jan", income: 240, expenditure: 220 },
  { month: "Mar", income: 280, expenditure: 260 },
  { month: "May", income: 310, expenditure: 290 },
  { month: "Jul", income: 295, expenditure: 285 },
  { month: "Sep", income: 330, expenditure: 310 },
  { month: "Nov", income: 360, expenditure: 345 }
];

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
  { value: "Social/Human Services", label: "Social Services" },
  { value: "Environment/Climate", label: "Climate & Environment" },
  { value: "Youth/Children Development", label: "Children & Youth" },
  { value: "Food, Agriculture & Nutrition", label: "Food & Nutrition" },
  { value: "tech-enablement", label: "Tech Enablement" },
  { value: "Sciences & Research", label: "Sciences & Research" },
  { value: "Health", label: "Health" },
  { value: "Arts & Culture", label: "Arts & Culture" },
  { value: "Humanitarian & Disaster Relief", label: "Humanitarian & Disaster" }
];

const REGIONS = ["London", "South East", "North West", "West Midlands", "South West", "Scotland", "Wales", "Northern Ireland"];

export default function App() {
  const [activeTab, setActiveTab] = useState<"overview" | "directory" | "admin">("overview");

  // Data states
  const [stats, setStats] = useState<KPIStats>(MOCK_STATS);
  const [charities, setCharities] = useState<Charity[]>(MOCK_CHARITIES);
  const [mapData, setMapData] = useState<GrantMapItem[]>(MOCK_MAP);
  const [selectedCharity, setSelectedCharity] = useState<Charity | null>(null);
  const [selectedCharityDetail, setSelectedCharityDetail] = useState<any>(null);
  const [charityGrants, setCharityGrants] = useState<GrantDetail[]>([]);
  const [sankeyData, setSankeyData] = useState<SankeyData | null>(null);

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
    fetchMapData();
  }, [selectedTags, selectedFoundationRegions, selectedRecipientRegions, annualGivingIndex, avgGrantSizeIndex]);

  useEffect(() => {
    const debounce = window.setTimeout(() => {
      fetchCharities();
    }, 350);
    return () => window.clearTimeout(debounce);
  }, [searchTerm]);

  useEffect(() => {
    if (selectedCharity) {
      setNewsSummary(null);
      setNewsError(null);
      setNewsLoading(false);
      fetchCharityDetail(selectedCharity.registered_charity_number);
      fetchCharityGrants(selectedCharity.registered_charity_number);
      fetchSankeyData(selectedCharity.registered_charity_number);
    } else {
      setSelectedCharityDetail(null);
      setNewsSummary(null);
      setNewsError(null);
      setNewsLoading(false);
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
        body: JSON.stringify({ username: "admin", password: "password" }),
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
            fetchMapData(true)
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
      } else {
        setApiError(`Organization detail request failed (${resp.status}).`);
      }
    } catch (e) {
      console.error("Failed to fetch charity details", e);
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

  const fetchMapData = async (forceOnline?: boolean) => {
    const isOnline = forceOnline !== undefined ? forceOnline : isBffOnline;
    if (!isOnline) return;
    try {
      const resp = await fetch(`${API_BASE}/api/charities/grants/map`, { credentials: "include" });
      if (resp.ok) {
        const data = await resp.json();
        setMapData(data);
      } else {
        setApiError(`Map-data request failed (${resp.status}).`);
      }
    } catch (e) {
      console.error("Failed to fetch map metrics", e);
      setApiError("Map data is temporarily unavailable.");
    }
  };

  const fetchCharityGrants = async (id: number) => {
    if (!isBffOnline) {
      // Mock grants made / received
      setCharityGrants([
        {
          grant_id: "360G-SEED-01",
          funding_charity_id: 326568,
          recipient_name: "Oxfam GB",
          recipient_charity_id: 202918,
          amount_eur: 250000.0,
          currency: "GBP",
          description: "Capacity building and disaster preparedness program support.",
          date: "2024-04-12",
          recipient_region: "Oxford",
          tags: ["Humanitarian & Disaster Relief", "Socio-economic Development, Poverty"]
        }
      ]);
      return;
    }
    try {
      const resp = await fetch(`${API_BASE}/api/charities/${id}/grants`, { credentials: "include" });
      if (resp.ok) {
        const data = await resp.json();
        setCharityGrants(data);
      }
    } catch (e) {
      console.error("Failed to fetch grants", e);
    }
  };

  const fetchSankeyData = async (id: number) => {
    if (!isBffOnline) {
      // Static mock layout
      setSankeyData({
        nodes: [
          { name: "Grants Received" },
          { name: "Other Income" },
          { name: "Oxfam GB" },
          { name: "Total Expenditure" },
          { name: "Reserves Surplus" },
          { name: "Grants Made" },
          { name: "Operating Expenses" }
        ],
        links: [
          { source: 0, target: 2, value: 20000000 },
          { source: 1, target: 2, value: 310000000 },
          { source: 2, target: 3, value: 300000000 },
          { source: 2, target: 4, value: 30000000 },
          { source: 3, target: 5, value: 80000000 },
          { source: 3, target: 6, value: 220000000 }
        ]
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
          nodes: data.nodes.map((n: any) => ({ name: n.label })),
          links: formattedLinks
        });
      }
    } catch (e) {
      console.error("Failed to fetch sankey metrics", e);
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


  const formatCurrency = (val: number) => {
    if (val >= 1000000) return `£${(val / 1000000).toFixed(1)}M`;
    if (val >= 1000) return `£${(val / 1000).toFixed(0)}k`;
    return `£${val}`;
  };

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
            <Activity size={24} />
            <span>Netlight x TUM</span>
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
              <span>Charities Directory</span>
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
            {activeTab === "directory" && "Charities Registry Directory"}
            {activeTab === "admin" && "Administrative Pipeline Monitor"}
          </h1>
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
            <div className="flex-col-gap">
              {/* KPIs */}
              <div className="grid-cols-4">
                <div className="glass-card kpi-card">
                  <div className="kpi-icon"><Building2 size={24} /></div>
                  <div className="kpi-value-container">
                    <span className="kpi-label">Registered Charities</span>
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
                    <span className="kpi-value">2,576</span>
                  </div>
                </div>
              </div>

              {/* Map and Thematic allocations */}
              <div className="grid-cols-1-2">
                {/* SVG Visual Map Outline (Replacement for Leaflet pins error) */}
                <div className="glass-card" style={{ display: "flex", flexDirection: "column", gap: "16px", minHeight: "450px" }}>
                  <h3 style={{ fontSize: "16px", fontWeight: "600" }}>UK Regional Funding Map</h3>
                  <div style={{ display: "flex", flexGrow: "1", position: "relative", justifyContent: "center", alignItems: "center" }}>
                    {/* SVG outline of the UK */}
                    <svg viewBox="0 0 400 500" style={{ height: "350px", width: "100%", opacity: 0.85 }}>
                      <path
                        d="M150,100 L160,80 L180,60 L200,80 L220,110 L240,130 L220,150 L200,160 L180,180 L170,220 L190,260 L180,280 L160,300 L150,330 L160,360 L180,380 L190,420 L160,430 L140,410 L120,380 L100,360 L80,350 L70,320 L100,300 L120,290 L110,240 L130,220 L140,180 L130,160 L140,130 Z"
                        fill="var(--nl-ash-dark)"
                        stroke="var(--border-glass)"
                        strokeWidth="1.5"
                      />
                      {/* Pulse indicators on active regions */}
                      {mapData.map((reg, idx) => {
                        // Approximate coordinate offsets
                        let x = 180;
                        let y = 350;
                        if (reg.region.includes("London")) { x = 200; y = 390; }
                        else if (reg.region.includes("North West")) { x = 160; y = 290; }
                        else if (reg.region.includes("South East")) { x = 210; y = 405; }
                        else if (reg.region.includes("Scotland")) { x = 170; y = 140; }
                        else if (reg.region.includes("Wales")) { x = 130; y = 340; }

                        const size = Math.max(12, Math.min(30, reg.total_amount_eur / 5000000));

                        return (
                          <g key={idx}>
                            <circle
                              cx={x}
                              cy={y}
                              r={size}
                              fill="var(--nl-unicorn-glow)"
                              stroke="var(--nl-unicorn)"
                              strokeWidth="2"
                            />
                            <circle
                              cx={x}
                              cy={y}
                              r={size + 4}
                              fill="none"
                              stroke="var(--nl-unicorn)"
                              strokeWidth="1"
                              opacity="0.35"
                              className="pulse-ring"
                            />
                            <text x={x} y={y - size - 4} fill="var(--text-primary)" fontSize="10" textAnchor="middle" fontWeight="bold">
                              {reg.region}
                            </text>
                          </g>
                        );
                      })}
                    </svg>

                    <div style={{ position: "absolute", bottom: "10px", left: "10px", display: "flex", flexDirection: "column", gap: "6px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "11px" }}>
                        <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "var(--nl-unicorn)", display: "inline-block" }}></span>
                        <span>Seeded Funding Node</span>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Thematic Recharts Area Chart */}
                <div className="glass-card" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                  <h3 style={{ fontSize: "16px", fontWeight: "600" }}>Monthly Funding Trends</h3>
                  <div style={{ width: "100%", height: "350px" }}>
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={MOCK_TRENDS_DATA}>
                        <defs>
                          <linearGradient id="colorIncome" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor="var(--nl-unicorn)" stopOpacity={0.3} />
                            <stop offset="95%" stopColor="var(--nl-unicorn)" stopOpacity={0} />
                          </linearGradient>
                          <linearGradient id="colorExpenditure" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor="var(--nl-sunny)" stopOpacity={0.2} />
                            <stop offset="95%" stopColor="var(--nl-sunny)" stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" />
                        <XAxis dataKey="month" stroke="var(--text-muted)" fontSize={11} />
                        <YAxis stroke="var(--text-muted)" fontSize={11} />
                        <Tooltip contentStyle={{ backgroundColor: "var(--bg-surface-opaque)", borderColor: "var(--border-glass)" }} />
                        <Area type="monotone" dataKey="income" name="Income (Millions)" stroke="var(--nl-unicorn)" fillOpacity={1} fill="url(#colorIncome)" />
                        <Area type="monotone" dataKey="expenditure" name="Expenses (Millions)" stroke="var(--nl-sunny)" fillOpacity={1} fill="url(#colorExpenditure)" />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              </div>

              {/* Bar Charts for projects */}
              <div className="glass-card">
                <h3 style={{ fontSize: "16px", fontWeight: "600", marginBottom: "20px" }}>Thematic Allocations (Share of Seed Funding)</h3>
                <div style={{ width: "100%", height: "240px" }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={MOCK_THEMATIC_DATA}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" />
                      <XAxis dataKey="name" stroke="var(--text-muted)" fontSize={11} />
                      <YAxis stroke="var(--text-muted)" fontSize={11} />
                      <Tooltip contentStyle={{ backgroundColor: "var(--bg-surface-opaque)", borderColor: "var(--border-glass)" }} />
                      <Bar dataKey="amount" fill="var(--nl-unicorn)" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
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
                      placeholder="Charity name..."
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
                    {REGIONS.map((reg) => (
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
                  <span className="filter-label">Donation Destination</span>
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
                    {REGIONS.map((reg) => (
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
                  onClick={() => {
                    setSearchTerm("");
                    setSelectedTags([]);
                    setSelectedFoundationRegions([]);
                    setSelectedRecipientRegions([]);
                    setAnnualGivingIndex(0);
                    setAvgGrantSizeIndex(0);
                  }}
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
                          <span className="charity-card-id">#{ch.registered_charity_number}</span>
                          <h3 className="charity-card-name">{ch.charity_name}</h3>
                        </div>
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
                      <div className="glass-card" style={{ gridColumn: "1 / -1", textAlign: "center", color: "var(--text-secondary)" }}>
                        No organizations match the current filters.
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
                <span className="charity-card-id">#{selectedCharity.registered_charity_number}</span>
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

            {/* Inflow vs Outflow Sankey Flow Widget */}
            <div>
              <h3 style={{ fontSize: "15px", fontWeight: "600", marginBottom: "12px", color: "var(--text-secondary)" }}>Balanced Inflow & Outflow Sankey Flow</h3>
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
                <div style={{ padding: "20px", textAlign: "center", color: "var(--text-muted)" }}>Calculating Sankey flows...</div>
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
              <h3 style={{ fontSize: "15px", fontWeight: "600", marginBottom: "12px", color: "var(--text-secondary)" }}>Recent Grants Transaction Log</h3>
              <div className="table-container" style={{ maxHeight: "250px", overflowY: "auto" }}>
                <table className="custom-table">
                  <thead>
                    <tr>
                      <th>Grant ID</th>
                      <th>Funder / Recipient Name</th>
                      <th>Amount (EUR)</th>
                      <th>Description</th>
                      <th>Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {charityGrants.map((gr, idx) => (
                      <tr key={idx}>
                        <td style={{ fontFamily: "var(--font-mono)", fontSize: "12px" }}>{gr.grant_id}</td>
                        <td>{gr.funding_charity_id === selectedCharity.registered_charity_number ? gr.recipient_name : "Institutional Grant Link"}</td>
                        <td style={{ fontWeight: "600", color: "var(--nl-unicorn)" }}>{formatCurrency(gr.amount_eur)}</td>
                        <td style={{ fontSize: "12px", color: "var(--text-secondary)" }}>{gr.description}</td>
                        <td style={{ whiteSpace: "nowrap" }}>{gr.date}</td>
                      </tr>
                    ))}
                    {charityGrants.length === 0 && (
                      <tr>
                        <td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)" }}>No grants recorded for this entity.</td>
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
