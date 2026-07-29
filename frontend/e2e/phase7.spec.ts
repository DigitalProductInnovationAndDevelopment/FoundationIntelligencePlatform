import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

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
  metadata: { data_mode: "playwright_test", source: ["360Giving"], record_count: 1300, limitations: [] },
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

const responses = {
  health: { status: "ok" },
  stats: {
    total_charities: 42,
    active_charities: 40,
    removed_charities: 2,
    average_income: 1_000_000,
    average_expenditure: 800_000,
    source: ["360Giving", "Charity Commission for England and Wales", "Philea"],
  },
  overview,
  geographies: ["United Kingdom", "Germany", "France"],
  entitySuggestions: { status: "available", donors: [], recipients: [] },
  connections: { status: "available", connections: [], connection_grant_count: 0 },
  registry: { results: [], next_cursor: null, has_more: false, page_size: 50, search_strategy: "indexed_directory" },
};

async function installApiRoutes(page: Page) {
  const requestCounts = new Map<string, number>();
  await page.route("**/health", async route => {
    const key = new URL(route.request().url()).pathname;
    requestCounts.set(key, (requestCounts.get(key) || 0) + 1);
    await route.fulfill({ json: responses.health });
  });
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    const key = `${url.pathname}${url.search}`;
    requestCounts.set(key, (requestCounts.get(key) || 0) + 1);
    let body: unknown;
    if (url.pathname === "/api/charities/stats") body = responses.stats;
    else if (url.pathname === "/api/charities/grants/overview") body = responses.overview;
    else if (url.pathname === "/api/charities/grants/beneficiary-geographies") body = responses.geographies;
    else if (url.pathname === "/api/charities/grants/overview/entity-suggestions") body = responses.entitySuggestions;
    else if (url.pathname === "/api/charities/grants/map/connections") body = responses.connections;
    else if (url.pathname === "/api/charities/directory/organizations") body = responses.registry;
    else body = { detail: `Unexpected local test route: ${url.pathname}` };
    await route.fulfill({ status: body === responses.registry || !Object.hasOwn(body as object, "detail") ? 200 : 404, json: body });
  });
  return requestCounts;
}

function captureBrowserProblems(page: Page) {
  const problems: string[] = [];
  page.on("console", message => {
    if (message.type() === "warning" || message.type() === "error") problems.push(`${message.type()}: ${message.text()}`);
  });
  page.on("pageerror", error => problems.push(`pageerror: ${error.message}`));
  return problems;
}

async function expectNoHorizontalOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    content: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
    viewport: window.innerWidth,
  }));
  expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport + 1);
}

test("map-first overview is responsive, accessible and request-bounded", async ({ page }, testInfo) => {
  const requestCounts = await installApiRoutes(page);
  const browserProblems = captureBrowserProblems(page);
  await page.goto("/");
  await expect(page.locator(".world-map-card")).toBeVisible();

  await expectNoHorizontalOverflow(page);
  const layout = await page.evaluate(() => {
    const viewportWidth = window.innerWidth;
    const inside = (element: Element) => {
      const bounds = element.getBoundingClientRect();
      return bounds.left >= -1 && bounds.right <= viewportWidth + 1;
    };
    const visibleControls = [...document.querySelectorAll("button, input, select, summary, a")]
      .filter(element => {
        const bounds = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return bounds.width > 0 && bounds.height > 0 && bounds.right > 0 && bounds.left < viewportWidth
          && bounds.bottom > 0 && bounds.top < window.innerHeight && style.visibility !== "hidden";
      });
    const accessibleName = (element: Element) => {
      const labels = "labels" in element
        ? [...((element as HTMLInputElement).labels || [])].map(label => label.textContent || "").join(" ").trim()
        : "";
      return element.getAttribute("aria-label") || labels || element.textContent?.trim() || element.getAttribute("title") || "";
    };
    const mapCard = document.querySelector(".world-map-card");
    const analytics = document.querySelector(".overview-analytics-grid");
    return {
      clippedControls: visibleControls.filter(element => !inside(element)).length,
      unnamedControls: visibleControls.filter(element => !accessibleName(element)).length,
      croppedKpis: [...document.querySelectorAll(".overview-kpi")]
        .filter(element => element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1).length,
      mapBeforeAnalytics: Boolean(mapCard && analytics && (mapCard.compareDocumentPosition(analytics) & Node.DOCUMENT_POSITION_FOLLOWING)),
      mapControlsInside: [...document.querySelectorAll(".map-mode-control button")].every(inside),
    };
  });
  expect(layout).toEqual({
    clippedControls: 0,
    unnamedControls: 0,
    croppedKpis: 0,
    mapBeforeAnalytics: true,
    mapControlsInside: true,
  });

  const overviewRequestCount = [...requestCounts.entries()]
    .filter(([key]) => key.startsWith("/api/charities/grants/overview?"))
    .reduce((total, [, count]) => total + count, 0);
  expect(overviewRequestCount).toBe(1);

  if (testInfo.project.name === "chrome-320") {
    await page.getByRole("button", { name: "Connections" }).click();
    await expect.poll(() => [...requestCounts.entries()]
      .filter(([key]) => key.startsWith("/api/charities/grants/map/connections?"))
      .reduce((total, [, count]) => total + count, 0)).toBe(1);
  }

  const filterTrigger = page.locator(".app-header-filter");
  await filterTrigger.focus();
  await filterTrigger.click();
  const drawer = page.locator(".overview-filter-drawer");
  await expect(drawer).toBeVisible();
  expect(await drawer.evaluate(panel => panel.contains(document.activeElement))).toBe(true);
  const drawerControls = drawer.locator("button:not([disabled]), input:not([disabled]), select:not([disabled])");
  await drawerControls.last().focus();
  await page.keyboard.press("Tab");
  await expect(drawerControls.first()).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(filterTrigger).toBeFocused();

  const axe = await new AxeBuilder({ page }).analyze();
  expect(axe.violations, JSON.stringify(axe.violations, null, 2)).toEqual([]);
  expect(browserProblems).toEqual([]);
});

test("Donor and Registry empty-state journey remains keyboard-safe", async ({ page }, testInfo) => {
  test.skip(!["chrome-320", "chrome-1024"].includes(testInfo.project.name), "Representative mobile and tablet/desktop widths cover this secondary journey.");
  await installApiRoutes(page);
  const browserProblems = captureBrowserProblems(page);
  await page.goto("/");
  await expect(page.locator(".world-map-card")).toBeVisible();

  if (testInfo.project.name === "chrome-320") await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByTitle("Donor Directory").click();
  await expect(page.locator(".donor-directory-page")).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.getByRole("button", { name: /Advanced Charity Commission Search/ }).click();
  await expect(page.locator(".registry-directory")).toBeVisible();
  await expect(page.getByRole("heading", { name: "No registry organizations found" })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const filterTrigger = page.locator(".app-header-filter");
  await filterTrigger.focus();
  await filterTrigger.click();
  const drawer = page.locator(".registry-filter-drawer");
  await expect(drawer).toBeVisible();
  expect(await drawer.evaluate(panel => panel.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(filterTrigger).toBeFocused();

  const axe = await new AxeBuilder({ page }).analyze();
  expect(axe.violations, JSON.stringify(axe.violations, null, 2)).toEqual([]);
  expect(browserProblems).toEqual([]);
});
