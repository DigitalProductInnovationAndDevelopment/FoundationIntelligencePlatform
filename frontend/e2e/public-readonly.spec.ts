import { expect, test } from "@playwright/test";


const emptyMap = {
  status: "available",
  geographic_dimension: "beneficiary",
  items: [],
  known_geography_count: 0,
  unknown_geography_count: 0,
  coverage_percentage: 0,
  currencies: ["EUR"],
  selected_currency: "EUR",
  funding_status: "available",
  funding_mode_available: true,
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
  minimum_coverage_threshold: 0,
  metadata: { data_mode: "public_readonly_browser_test", source: ["fixture"], record_count: 0, limitations: [] },
};

const emptyOverview = {
  status: "available",
  kpis: {
    awarded_funding: 0,
    currency: "EUR",
    grants_monitored: 0,
    country_coverage_percentage: 0,
    mapped_grant_count: 0,
    unmapped_grant_count: 0,
    programme_coverage_percentage: 0,
    classified_grant_count: 0,
    qualifying_programme_grant_count: 0,
  },
  map: emptyMap,
  trends: { status: "available", currency: "EUR", available_currencies: ["EUR"], granularity: "monthly", period: null, items: [], excluded: {}, last_refreshed_at: null },
  themes: { status: "available", currency: "EUR", items: [], classification_coverage: { classified_percentage: 0, classified_grant_count: 0, unclassified_grant_count: 0 } },
  available_date_range: { from: null, to: null },
};

test("anonymous demo exposes the reviewed read-only UI routes", async ({ context, page }) => {
  await page.route("**/api/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const mutation = request.method() !== "GET" && request.method() !== "HEAD";
    const protectedPath = url.pathname.startsWith("/api/admin/");
    if (mutation || protectedPath) {
      await route.fulfill({ status: 401, json: { detail: "Authentication required." } });
      return;
    }
    if (url.pathname === "/api/auth/config") {
      await route.fulfill({ status: 200, json: { mode: "public_readonly" } });
      return;
    }
    if (url.pathname === "/api/charities/grants/overview") {
      await route.fulfill({ status: 200, json: emptyOverview });
      return;
    }
    if (url.pathname === "/api/charities/stats") {
      await route.fulfill({ status: 200, json: { total_charities: 0, active_charities: 0, removed_charities: 0, average_income: 0, average_expenditure: 0, source: ["fixture"] } });
      return;
    }
    if (url.pathname === "/api/charities/grants/beneficiary-geographies") {
      await route.fulfill({ status: 200, json: [] });
      return;
    }
    await route.fulfill({ status: 200, json: { items: [] } });
  });
  await page.route("**/health", route => route.fulfill({ status: 200, json: { status: "ok" } }));

  const overviewResponsePromise = page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.pathname === "/api/charities/grants/overview"
      && response.request().method() === "GET";
  });

  await page.goto("/");
  const overviewResponse = await overviewResponsePromise;
  expect(overviewResponse.status()).toBe(200);
  expect(overviewResponse.headers()["set-cookie"]).toBeUndefined();
  await expect(page.locator(".world-map-card")).toBeVisible();

  const results = await page.evaluate(async () => {
    const admin = await fetch("/api/admin/pipeline/status", { credentials: "omit" });
    const directory = await fetch("/api/charities", { credentials: "omit" });
    const mutation = await fetch("/api/admin/pipeline/trigger", {
      method: "POST",
      credentials: "omit",
      headers: { "Content-Type": "application/json", "Idempotency-Key": "public-demo-browser-test" },
      body: JSON.stringify({ source: "quick_consolidate" }),
    });
    return {
      admin: admin.status,
      directory: directory.status,
      directoryCookie: directory.headers.get("set-cookie"),
      mutation: mutation.status,
    };
  });
  expect(results).toEqual({ admin: 401, directory: 200, directoryCookie: null, mutation: 401 });
  expect(await context.cookies()).toEqual([]);
});
