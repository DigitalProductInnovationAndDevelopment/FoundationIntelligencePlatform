import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const source = (relativePath: string) => readFileSync(join(process.cwd(), relativePath), "utf8");

test("the frontend has no runtime CDN stylesheet or console warning/error paths", () => {
  const css = source("src/index.css");
  const app = source("src/App.tsx");
  const components = [
    "src/components/OverviewDashboard.tsx",
    "src/components/GrantWorldMap.tsx",
    "src/components/DonorDirectoryPage.tsx",
    "src/components/RegistryDirectory.tsx",
  ].map(source).join("\n");

  assert.doesNotMatch(css, /@import\s+url\(["']?https?:/i);
  assert.doesNotMatch(`${app}\n${components}`, /console\.(?:warn|error)\s*\(/);
});

test("heavy map, overview and chart code remains behind lazy boundaries", () => {
  const app = source("src/App.tsx");

  assert.match(app, /lazy\(\(\) => import\("\.\/components\/GrantWorldMap"\)\)/);
  assert.match(app, /lazy\(\(\) => import\("\.\/components\/OverviewDashboard"\)\)/);
  assert.match(app, /import\("\.\/components\/DataCharts"\)/);
  assert.doesNotMatch(app, /from\s+["']recharts["']/);
});

test("request lifecycles cancel stale work and load map connections on demand", () => {
  const app = source("src/App.tsx");
  const overview = source("src/components/OverviewDashboard.tsx");

  assert.match(app, /mapRequestRef\.current\?\.abort\(\)/);
  assert.match(app, /grantAnalyticsRequestRef\.current\?\.abort\(\)/);
  assert.match(overview, /new AbortController\(\)/);
  assert.match(overview, /api\/charities\/grants\/map\/connections/);
  assert.doesNotMatch(overview, /include_connections/);
  assert.match(overview, /return \(\) => controller\.abort\(\)/);
});

test("stable semantic keys replace index-derived evidence and legend keys", () => {
  const app = source("src/App.tsx");
  const donorDirectory = source("src/components/DonorDirectoryPage.tsx");
  const worldMap = source("src/components/GrantWorldMap.tsx");

  assert.doesNotMatch(app, /source\.link\}.*index/);
  assert.doesNotMatch(donorDirectory, /evidence\.url\}.*index/);
  assert.match(donorDirectory, /key=\{evidenceKey\(evidence\)\}/);
  assert.doesNotMatch(worldMap, /key=\{`\$\{label\}-\$\{index\}`\}/);
});

test("the responsive shell preserves the map-first layout and viewport constraints", () => {
  const app = source("src/App.tsx");
  const overview = source("src/components/OverviewDashboard.tsx");
  const css = source("src/index.css");

  assert.match(app, /className="skip-link" href="#main-content"/);
  assert.match(app, /id="main-content" className="main-content" tabIndex=\{-1\}/);
  assert.ok(overview.indexOf("<GrantWorldMap") < overview.indexOf("overview-analytics-grid"));
  assert.match(css, /\.app-container\s*\{[^}]*width:\s*100%;[^}]*max-width:\s*100%/s);
  assert.match(css, /@media \(max-width: 520px\)[\s\S]*\.map-mode-control\s*\{[^}]*flex-wrap:\s*wrap/);
  assert.doesNotMatch(app, /if\s*\(initialLoading\)\s*return/);
});
