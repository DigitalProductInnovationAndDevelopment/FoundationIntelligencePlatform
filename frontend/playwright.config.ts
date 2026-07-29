import { existsSync } from "node:fs";
import { defineConfig } from "@playwright/test";

const defaultChrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const chromeExecutable = process.env.PHASE7_CHROME_PATH || defaultChrome;

if (!existsSync(chromeExecutable)) {
  throw new Error(`Local Chrome executable not found at ${chromeExecutable}. Set PHASE7_CHROME_PATH to an installed Chrome/Chromium binary.`);
}

const viewports = [320, 390, 768, 1024, 1440, 1920];

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:4174",
    browserName: "chromium",
    headless: true,
    launchOptions: {
      executablePath: chromeExecutable,
      args: [
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-sync",
        "--metrics-recording-only",
        "--no-first-run",
        "--no-default-browser-check",
      ],
    },
  },
  projects: viewports.map(width => ({
    name: `chrome-${width}`,
    use: { viewport: { width, height: 1000 } },
  })),
  webServer: {
    command: "npm run preview -- --host 127.0.0.1 --port 4174 --strictPort",
    url: "http://127.0.0.1:4174",
    reuseExistingServer: false,
    timeout: 15_000,
  },
});
