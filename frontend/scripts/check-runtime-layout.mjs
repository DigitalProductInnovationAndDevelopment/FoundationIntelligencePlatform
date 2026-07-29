import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const WIDTHS = process.env.PHASE7_WIDTHS
  ? process.env.PHASE7_WIDTHS.split(",").map(Number)
  : [320, 390, 768, 1024, 1440, 1920];
const HEIGHT = 1000;
const BASE_URL = "http://127.0.0.1:4173";
const chromeCandidates = [
  process.env.CHROME_PATH,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].filter(Boolean);
const chromePath = chromeCandidates.find(candidate => existsSync(candidate));

if (!chromePath) throw new Error("A local Chrome or Chromium binary is required for the runtime layout gate.");

const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
const children = [];
const chromeProfile = mkdtempSync(join(tmpdir(), "fip-phase7-chrome-"));

function stopChildren() {
  for (const child of children) {
    if (!child.killed) child.kill("SIGTERM");
  }
  rmSync(chromeProfile, { recursive: true, force: true });
}

process.once("exit", stopChildren);
process.once("SIGINT", () => process.exit(130));
process.once("SIGTERM", () => process.exit(143));

async function waitForPreview() {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(BASE_URL);
      if (response.ok) return;
    } catch {
      // The local preview process is still starting.
    }
    await sleep(50);
  }
  throw new Error("The local Vite preview did not become ready.");
}

class CdpClient {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 1;
    this.pending = new Map();
    this.events = [];
    socket.addEventListener("message", event => {
      const message = JSON.parse(String(event.data));
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(message.error.message));
        else pending.resolve(message.result);
        return;
      }
      this.events.push(message);
    });
  }

  send(method, params = {}) {
    const id = this.nextId;
    this.nextId += 1;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }
}

async function evaluate(client, expression) {
  const result = await client.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "Browser evaluation failed.");
  return result.result.value;
}

async function waitFor(client, expression, message) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (await evaluate(client, expression)) return;
    await sleep(50);
  }
  throw new Error(message);
}

const map = {
  status: "available",
  geographic_dimension: "beneficiary",
  items: [{
    region_or_country_code: "GB",
    region_or_country_name: "United Kingdom",
    grant_count: 1250,
    total_amount: 123456789,
    currency: "EUR",
    distinct_funders: 84,
    distinct_recipients: 720,
    top_programme_areas: [],
    top_funders: [],
    top_recipients: [],
    original_geographies: ["United Kingdom"],
    funding_grant_count: 1200,
    excluded_multi_country_grant_count: 0,
    excluded_invalid_amount_grant_count: 0,
  }],
  known_geography_count: 1250,
  unknown_geography_count: 50,
  coverage_percentage: 96.2,
  currencies: ["EUR"],
  selected_currency: "EUR",
  funding_status: "available",
  funding_mode_available: true,
  grant_country_association_count: 1250,
  multi_country_grant_count: 0,
  funding_excluded_multi_country_count: 0,
  funding_excluded_multi_country_amount: 0,
  funding_excluded_currency_count: 0,
  funding_excluded_invalid_amount_count: 0,
  connections: [],
  connection_grant_count: 0,
  connection_excluded_no_headquarters_count: 0,
  connection_same_country_count: 0,
  minimum_coverage_threshold: 0,
  metadata: { data_mode: "runtime_test", source: ["360Giving"], record_count: 1300, limitations: [] },
};

const overview = {
  status: "available",
  kpis: {
    awarded_funding: 123456789,
    currency: "EUR",
    grants_monitored: 1300,
    country_coverage_percentage: 96.2,
    mapped_grant_count: 1250,
    unmapped_grant_count: 50,
    programme_coverage_percentage: 87.5,
    classified_grant_count: 1138,
    qualifying_programme_grant_count: 1300,
  },
  map,
  trends: {
    status: "available",
    currency: "EUR",
    available_currencies: ["EUR"],
    granularity: "monthly",
    period: null,
    items: [],
    excluded: {},
    last_refreshed_at: null,
  },
  themes: {
    status: "available",
    currency: "EUR",
    items: [],
    classification_coverage: { classified_percentage: 87.5, classified_grant_count: 1138, unclassified_grant_count: 162 },
  },
  available_date_range: { from: "2024-01-01", to: "2026-06-30" },
};

const browserMock = `(() => {
  const responses = {
    health: ${JSON.stringify({ status: "ok" })},
    stats: ${JSON.stringify({ total_charities: 42, active_charities: 40, removed_charities: 2, average_income: 1000000, average_expenditure: 800000, source: ["360Giving", "Charity Commission for England and Wales", "Philea"] })},
    overview: ${JSON.stringify(overview)},
    geographies: ${JSON.stringify(["United Kingdom", "Germany", "France"])},
    connections: ${JSON.stringify({ status: "available", connections: [], connection_grant_count: 0 })},
    registry: ${JSON.stringify({ results: [], next_cursor: null, has_more: false, page_size: 50, search_strategy: "indexed_directory" })},
  };
  window.__phase7Requests = {};
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = new URL(typeof input === "string" ? input : input.url, window.location.href);
    const key = url.pathname + url.search;
    window.__phase7Requests[key] = (window.__phase7Requests[key] || 0) + 1;
    let body;
    if (url.pathname === "/health") body = responses.health;
    else if (url.pathname === "/api/charities/stats") body = responses.stats;
    else if (url.pathname === "/api/charities/grants/overview") body = responses.overview;
    else if (url.pathname === "/api/charities/grants/beneficiary-geographies") body = responses.geographies;
    else if (url.pathname === "/api/charities/grants/map/connections") body = responses.connections;
    else if (url.pathname === "/api/charities/directory/organizations") body = responses.registry;
    else return nativeFetch(input, init);
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  };
})();`;

let socket;
try {
  const preview = spawn(process.execPath, ["node_modules/vite/bin/vite.js", "preview", "--host", "127.0.0.1", "--port", "4173", "--strictPort"], {
    stdio: ["ignore", "pipe", "pipe"],
  });
  children.push(preview);
  await waitForPreview();

  const chrome = spawn(chromePath, [
    "--headless=new",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-extensions",
    "--disable-sync",
    "--metrics-recording-only",
    "--no-first-run",
    "--no-default-browser-check",
    "--remote-debugging-port=0",
    `--user-data-dir=${chromeProfile}`,
    "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  children.push(chrome);

  const browserWebSocket = await new Promise((resolve, reject) => {
    let output = "";
    const timeout = setTimeout(() => reject(new Error("Chrome DevTools did not become ready.")), 10_000);
    chrome.stderr.on("data", chunk => {
      output += chunk.toString();
      const match = output.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (!match) return;
      clearTimeout(timeout);
      resolve(match[1]);
    });
    chrome.once("exit", code => reject(new Error(`Chrome exited before testing (code ${code}).`)));
  });

  const debugPort = new URL(browserWebSocket).port;
  const targets = await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json();
  const pageTarget = targets.find(target => target.type === "page");
  if (!pageTarget) throw new Error("Chrome did not expose a page target.");
  socket = new WebSocket(pageTarget.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  const client = new CdpClient(socket);
  await client.send("Page.enable");
  await client.send("Runtime.enable");
  await client.send("Log.enable");
  await client.send("Page.addScriptToEvaluateOnNewDocument", { source: browserMock });

  const failures = [];
  for (const width of WIDTHS) {
    const failureCountBeforeWidth = failures.length;
    client.events.length = 0;
    await client.send("Emulation.setDeviceMetricsOverride", {
      width,
      height: HEIGHT,
      deviceScaleFactor: 1,
      mobile: width < 768,
    });
    await client.send("Page.navigate", { url: BASE_URL });
    await waitFor(client, "document.readyState === 'complete' && Boolean(document.querySelector('.world-map-card'))", `Overview did not render at ${width}px.`);
    await sleep(150);

    const layout = await evaluate(client, `(() => {
      const viewportWidth = window.innerWidth;
      const rectInside = element => {
        const rect = element.getBoundingClientRect();
        return rect.left >= -1 && rect.right <= viewportWidth + 1;
      };
      const visibleControls = [...document.querySelectorAll('button, input, select, summary, a')]
        .filter(element => {
          const rect = element.getBoundingClientRect();
          const style = getComputedStyle(element);
          return rect.width > 0 && rect.height > 0 && rect.right > 0 && rect.left < viewportWidth && rect.bottom > 0 && rect.top < window.innerHeight && style.visibility !== 'hidden';
        });
      const accessibleName = element => {
        const labelledBy = (element.getAttribute('aria-labelledby') || '').split(' ').filter(Boolean)
          .map(id => document.getElementById(id)?.textContent || '').join(' ').trim();
        return element.getAttribute('aria-label')
          || labelledBy
          || [...(element.labels || [])].map(label => label.textContent || '').join(' ').trim()
          || element.textContent.trim()
          || element.getAttribute('title')
          || '';
      };
      const map = document.querySelector('.world-map-card');
      const analytics = document.querySelector('.overview-analytics-grid');
      return {
        scrollWidth: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
        viewportWidth,
        clippedControls: visibleControls.filter(element => !rectInside(element)).map(element => element.getAttribute('aria-label') || element.textContent.trim().slice(0, 50)),
        unnamedControls: visibleControls.filter(element => !accessibleName(element)).map(element => element.outerHTML.slice(0, 100)),
        croppedKpis: [...document.querySelectorAll('.overview-kpi')].filter(element => element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1).length,
        mapBeforeAnalytics: Boolean(map && analytics && (map.compareDocumentPosition(analytics) & Node.DOCUMENT_POSITION_FOLLOWING)),
        mapControls: [...document.querySelectorAll('.map-mode-control button')].every(rectInside),
        overviewRequests: Object.entries(window.__phase7Requests).filter(([key]) => key.startsWith('/api/charities/grants/overview?')).reduce((total, [, count]) => total + count, 0),
      };
    })()`);

    if (layout.scrollWidth > layout.viewportWidth + 1) failures.push(`${width}px: page scroll width ${layout.scrollWidth}px exceeds viewport ${layout.viewportWidth}px.`);
    if (layout.clippedControls.length) failures.push(`${width}px: clipped visible controls: ${layout.clippedControls.join(", ")}.`);
    if (layout.unnamedControls.length) failures.push(`${width}px: controls without accessible names: ${layout.unnamedControls.join(", ")}.`);
    if (layout.croppedKpis) failures.push(`${width}px: ${layout.croppedKpis} KPI card(s) crop their content.`);
    if (!layout.mapBeforeAnalytics) failures.push(`${width}px: map is not before the analytics panels.`);
    if (!layout.mapControls) failures.push(`${width}px: map controls leave the viewport.`);
    if (layout.overviewRequests !== 1) failures.push(`${width}px: expected one initial overview request, observed ${layout.overviewRequests}.`);

    if (width === 320) {
      await evaluate(client, "[...document.querySelectorAll('.map-mode-control button')].find(button => button.textContent.includes('Connections'))?.click()");
      await waitFor(client, "Object.entries(window.__phase7Requests).some(([key, count]) => key.startsWith('/api/charities/grants/map/connections?') && count === 1)", "The lazy map connection request did not run exactly once.");
    }

    await evaluate(client, "(() => { const trigger = document.querySelector('.app-header-filter'); trigger?.focus(); trigger?.click(); })()");
    await waitFor(client, "Boolean(document.querySelector('.overview-filter-drawer'))", `Filter drawer did not open at ${width}px.`);
    const drawer = await evaluate(client, `(() => {
      const panel = document.querySelector('.overview-filter-drawer');
      const rect = panel.getBoundingClientRect();
      const controls = [...panel.querySelectorAll('button, input, select')];
      return {
        insideViewport: rect.left >= -1 && rect.right <= window.innerWidth + 1 && rect.top >= -1 && rect.bottom <= window.innerHeight + 1,
        controlsInside: controls.every(control => { const bounds = control.getBoundingClientRect(); return bounds.left >= -1 && bounds.right <= window.innerWidth + 1; }),
        focusContained: panel.contains(document.activeElement),
      };
    })()`);
    if (!drawer.insideViewport || !drawer.controlsInside) failures.push(`${width}px: filter drawer or a drawer control leaves the viewport.`);
    if (!drawer.focusContained) failures.push(`${width}px: focus did not move into the filter drawer.`);
    await evaluate(client, "(() => { const controls = [...document.querySelectorAll('.overview-filter-drawer button:not([disabled]), .overview-filter-drawer input:not([disabled]), .overview-filter-drawer select:not([disabled])')]; controls.at(-1)?.focus(); })()");
    await client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab" });
    await client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab" });
    const focusWrapped = await evaluate(client, "(() => { const controls = [...document.querySelectorAll('.overview-filter-drawer button:not([disabled]), .overview-filter-drawer input:not([disabled]), .overview-filter-drawer select:not([disabled])')]; return document.activeElement === controls[0]; })()");
    if (!focusWrapped) failures.push(`${width}px: Tab did not wrap within the modal filter drawer.`);
    await client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape" });
    await client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape" });
    await waitFor(client, "!document.querySelector('.overview-filter-drawer')", `Filter drawer did not close with Escape at ${width}px.`);
    try {
      await waitFor(client, "document.activeElement?.classList.contains('app-header-filter')", `Focus did not return to the filter trigger at ${width}px.`);
    } catch {
      const activeElement = await evaluate(client, "({ tag: document.activeElement?.tagName, className: document.activeElement?.className || '', label: document.activeElement?.getAttribute('aria-label') || document.activeElement?.textContent?.trim().slice(0, 40) || '' })");
      failures.push(`${width}px: focus did not return to the filter trigger (active: ${activeElement.tag}.${activeElement.className} “${activeElement.label}”).`);
    }

    if (width === 320 || width === 1024) {
      await evaluate(client, "document.querySelector('.nav-item[title=\"Donor Directory\"]')?.click()");
      await waitFor(client, "Boolean(document.querySelector('.donor-directory-page'))", `Donor Directory did not render at ${width}px.`);
      const donorWidth = await evaluate(client, "Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)");
      if (donorWidth > width + 1) failures.push(`${width}px: Donor Directory scroll width ${donorWidth}px exceeds the viewport.`);
      await evaluate(client, "[...document.querySelectorAll('.donor-directory-secondary-links button')].find(button => button.textContent.includes('Advanced Charity Commission Search'))?.click()");
      await waitFor(client, "Boolean(document.querySelector('.registry-directory'))", `Registry Directory did not render at ${width}px.`);
      await waitFor(client, "Boolean(document.querySelector('.directory-empty-state'))", `Registry empty state did not render at ${width}px.`);
      const registryWidth = await evaluate(client, "Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)");
      if (registryWidth > width + 1) failures.push(`${width}px: Registry Directory scroll width ${registryWidth}px exceeds the viewport.`);
      await evaluate(client, "(() => { const trigger = document.querySelector('.app-header-filter'); trigger?.focus(); trigger?.click(); })()");
      await waitFor(client, "Boolean(document.querySelector('.registry-filter-drawer'))", `Registry filter drawer did not open at ${width}px.`);
      const registryDrawerInside = await evaluate(client, "(() => { const bounds = document.querySelector('.registry-filter-drawer').getBoundingClientRect(); return bounds.left >= -1 && bounds.right <= window.innerWidth + 1; })()");
      if (!registryDrawerInside) failures.push(`${width}px: Registry filter drawer leaves the viewport.`);
      await client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape" });
      await client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape" });
      await waitFor(client, "!document.querySelector('.registry-filter-drawer')", `Registry filter drawer did not close with Escape at ${width}px.`);
      await waitFor(client, "document.activeElement?.classList.contains('app-header-filter')", `Registry filter focus did not return at ${width}px.`);
    }

    const browserProblems = client.events.filter(event => (
      (event.method === "Runtime.exceptionThrown")
      || (event.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(event.params.type))
      || (event.method === "Log.entryAdded" && ["error", "warning"].includes(event.params.entry.level))
    ));
    if (browserProblems.length) failures.push(`${width}px: ${browserProblems.length} browser console warning/error event(s).`);
    if (failures.length === failureCountBeforeWidth) {
      console.log(`${width}px: no page overflow; map, filters, controls, focus, requests and console passed.`);
    }
  }

  if (failures.length) {
    for (const failure of failures) console.error(`Runtime layout failure: ${failure}`);
    process.exitCode = 1;
  } else {
    console.log("Runtime layout gate passed at 320, 390, 768, 1024, 1440 and 1920 pixels.");
  }
} finally {
  socket?.close();
  stopChildren();
}
