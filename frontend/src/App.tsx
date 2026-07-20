import { useState, useEffect, useRef } from "react";
import { 
  Building2, 
  TrendingUp, 
  Activity, 
  Database, 
  LogOut, 
  Search, 
  SlidersHorizontal, 
  Terminal, 
  Sparkles, 
  ArrowRight,
  TrendingDown,
  DollarSign,
  Play
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
  
  // Filter states
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedTag, setSelectedTag] = useState<string>("");
  const [selectedRegion, setSelectedRegion] = useState<string>("");
  const [selectedSize, setSelectedSize] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [isBffOnline, setIsBffOnline] = useState(false);
  
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
  const logsEndRef = useRef<HTMLDivElement>(null);

  // Fetch initial configuration on mount
  useEffect(() => {
    checkBffHealth();
  }, []);

  useEffect(() => {
    fetchStats();
    fetchCharities();
    fetchMapData();
  }, [selectedTag, selectedRegion, selectedSize]);

  useEffect(() => {
    if (selectedCharity) {
      fetchCharityDetail(selectedCharity.registered_charity_number);
      fetchCharityGrants(selectedCharity.registered_charity_number);
      fetchSankeyData(selectedCharity.registered_charity_number);
    } else {
      setSelectedCharityDetail(null);
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
        setIsBffOnline(true);
        console.log("Logged in to BFF successfully.");
      }
    } catch (e) {
      console.error("Auto login failed", e);
    }
  };

  const checkBffHealth = async () => {
    try {
      const resp = await fetch(`${API_BASE}/health`);
      if (resp.ok) {
        setIsBffOnline(true);
        console.log("BFF Backend is online. Authenticating...");
        await autoLogin();
        // Load initial live dataset
        fetchStats();
        fetchCharities();
        fetchMapData();
      }
    } catch {
      setIsBffOnline(false);
      console.warn("BFF offline. Falling back to mock dataset.");
    }
  };

  const fetchStats = async () => {
    if (!isBffOnline) return;
    try {
      const resp = await fetch(`${API_BASE}/api/charities/stats`, { credentials: "include" });
      if (resp.ok) {
        const data = await resp.json();
        setStats(data);
      }
    } catch (e) {
      console.error("Failed to fetch stats", e);
    }
  };

  const fetchCharities = async () => {
    setLoading(true);
    if (!isBffOnline) {
      // Offline local filtering of mocks
      let filtered = [...MOCK_CHARITIES];
      if (searchTerm) {
        filtered = filtered.filter(c => c.charity_name.toLowerCase().includes(searchTerm.toLowerCase()));
      }
      if (selectedSize) {
        filtered = filtered.filter(c => {
          const exp = c.latest_expenditure || 0;
          if (selectedSize === "small") return exp < 1000000;
          if (selectedSize === "medium") return exp >= 1000000 && exp <= 10000000;
          if (selectedSize === "large") return exp > 10000000;
          return true;
        });
      }
      setCharities(filtered);
      setLoading(false);
      return;
    }
    try {
      let url = `${API_BASE}/api/charities?limit=50`;
      if (searchTerm) url += `&search=${encodeURIComponent(searchTerm)}`;
      if (selectedTag) url += `&tag=${encodeURIComponent(selectedTag)}`;
      if (selectedRegion) url += `&region=${encodeURIComponent(selectedRegion)}`;
      if (selectedSize) url += `&size=${encodeURIComponent(selectedSize)}`;
      
      const resp = await fetch(url, { credentials: "include" });
      if (resp.ok) {
        const data = await resp.json();
        setCharities(data);
      }
    } catch (e) {
      console.error("Failed to fetch charities", e);
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
      }
    } catch (e) {
      console.error("Failed to fetch charity details", e);
    }
  };

  const fetchMapData = async () => {
    if (!isBffOnline) return;
    try {
      const resp = await fetch(`${API_BASE}/api/charities/grants/map`, { credentials: "include" });
      if (resp.ok) {
        const data = await resp.json();
        setMapData(data);
      }
    } catch (e) {
      console.error("Failed to fetch map metrics", e);
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
    if (!isBffOnline) {
      // Simulate run in logs
      setLogs(prev => prev + `\n[Simulating] Triggered run mode: ${source} (Limit: ${pipelineLimit}, Fresh: ${pipelineFresh ? "Yes" : "No"})...\n`);
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
          fresh: source === "quick_consolidate" ? undefined : pipelineFresh
        }),
        credentials: "include"
      });
      if (resp.ok) {
        const data = await resp.json();
        setPipelineStatus(data);
        setLogs(prev => prev + `\n[System] Predefined execution triggered successfully: ${source} (Limit: ${pipelineLimit}, Fresh: ${pipelineFresh ? "Yes" : "No"})\n`);
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

  return (
    <div className="app-container">
      {/* Sidebar Navigation */}
      <aside className="sidebar">
        <div>
          <div className="sidebar-logo">
            <Activity size={24} />
            <span>CharityHub</span>
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
            {activeTab === "overview" && "Financial Overview Dashboard"}
            {activeTab === "directory" && "Charities Registry Directory"}
            {activeTab === "admin" && "Administrative Pipeline Monitor"}
          </h1>
          <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
            <span style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
              Amplify Social Impact Data
            </span>
          </div>
        </header>

        {/* Dynamic Pages */}
        <div className="page-container">
          
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
                          <g key={idx} cursor="pointer" onClick={() => setSelectedRegion(reg.region)}>
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
                            <stop offset="5%" stopColor="var(--nl-unicorn)" stopOpacity={0.3}/>
                            <stop offset="95%" stopColor="var(--nl-unicorn)" stopOpacity={0}/>
                          </linearGradient>
                          <linearGradient id="colorExpenditure" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor="var(--nl-sunny)" stopOpacity={0.2}/>
                            <stop offset="95%" stopColor="var(--nl-sunny)" stopOpacity={0}/>
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
                      onChange={(e) => { setSearchTerm(e.target.value); fetchCharities(); }}
                    />
                  </div>
                </div>

                <div className="filter-group" style={{ marginTop: "12px" }}>
                  <span className="filter-label">Thematic Sector</span>
                  <select 
                    className="form-input"
                    value={selectedTag}
                    onChange={(e) => setSelectedTag(e.target.value)}
                  >
                    <option value="">All Sectors</option>
                    <option value="Social/Human Services">Social Services</option>
                    <option value="Environment/Climate">Climate & Environment</option>
                    <option value="Youth/Children Development">Children & Youth</option>
                    <option value="Food, Agriculture & Nutrition">Food & Nutrition</option>
                    <option value="tech-enablement">Tech Enablement</option>
                  </select>
                </div>

                <div className="filter-group" style={{ marginTop: "12px" }}>
                  <span className="filter-label">Geographic Region</span>
                  <select 
                    className="form-input"
                    value={selectedRegion}
                    onChange={(e) => setSelectedRegion(e.target.value)}
                  >
                    <option value="">All Regions</option>
                    <option value="London">London</option>
                    <option value="South East">South East</option>
                    <option value="North West">North West</option>
                    <option value="West Midlands">West Midlands</option>
                    <option value="South West">South West</option>
                    <option value="Scotland">Scotland</option>
                    <option value="Wales">Wales</option>
                    <option value="Northern Ireland">Northern Ireland</option>
                  </select>
                </div>

                <div className="filter-group" style={{ marginTop: "12px" }}>
                  <span className="filter-label">Size (Annual Giving)</span>
                  <select 
                    className="form-input"
                    value={selectedSize}
                    onChange={(e) => setSelectedSize(e.target.value)}
                  >
                    <option value="">All Sizes</option>
                    <option value="small">Small (&lt; €1M)</option>
                    <option value="medium">Medium (€1M - €10M)</option>
                    <option value="large">Large (&gt; €10M)</option>
                  </select>
                </div>

                <button 
                  className="btn btn-secondary" 
                  style={{ marginTop: "16px" }}
                  onClick={() => { setSearchTerm(""); setSelectedTag(""); setSelectedRegion(""); setSelectedSize(""); }}
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
                    <div style={{ display: "flex", justifyContent: "space-between", gap: "12px" }}>
                      <button 
                        className="btn btn-primary" 
                        style={{ flexGrow: "1" }}
                        disabled={isTriggering || pipelineStatus.status === "running"}
                        onClick={() => triggerPipeline("full_run")}
                      >
                        <Play size={16} />
                        Trigger Full Run
                      </button>
                      <button 
                        className="btn btn-secondary" 
                        style={{ flexGrow: "1" }}
                        disabled={isTriggering || pipelineStatus.status === "running"}
                        onClick={() => triggerPipeline("quick_consolidate")}
                      >
                        <Database size={16} />
                        Quick Consolidate
                      </button>
                    </div>

                    <div style={{ display: "flex", gap: "12px" }}>
                      <button 
                        className="btn btn-secondary" 
                        style={{ flexGrow: "1" }}
                        disabled={isTriggering || pipelineStatus.status === "running"}
                        onClick={() => triggerPipeline("refresh_charities")}
                      >
                        <Activity size={16} />
                        Scrape Charity Seeds
                      </button>
                      
                      <button 
                        className="btn btn-secondary" 
                        style={{ flexGrow: "1" }}
                        disabled={isTriggering || pipelineStatus.status === "running"}
                        onClick={() => triggerPipeline("refresh_grants")}
                      >
                        <Sparkles size={16} />
                        Scrape 360Giving Seeds
                      </button>
                    </div>

                    <div style={{ display: "flex", gap: "16px", marginTop: "12px", borderTop: "1px solid var(--border-glass)", paddingTop: "16px" }}>
                      <div className="filter-group" style={{ flex: 1 }}>
                        <span className="filter-label">Scraping Limit (Foundations)</span>
                        <input 
                          type="number" 
                          className="form-input" 
                          value={pipelineLimit} 
                          onChange={(e) => setPipelineLimit(parseInt(e.target.value) || 20)}
                          min="1"
                          max="200"
                        />
                      </div>
                      <div className="filter-group" style={{ flex: 1, justifyContent: "center" }}>
                        <label className="checkbox-label" style={{ marginTop: "20px" }}>
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
                          <stop offset="5%" stopColor="var(--nl-unicorn)" stopOpacity={0.2}/>
                          <stop offset="95%" stopColor="var(--nl-unicorn)" stopOpacity={0}/>
                        </linearGradient>
                        <linearGradient id="colorExp" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="var(--nl-sunny)" stopOpacity={0.2}/>
                          <stop offset="95%" stopColor="var(--nl-sunny)" stopOpacity={0}/>
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
