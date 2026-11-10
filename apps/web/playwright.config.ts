import { defineConfig, devices } from "@playwright/test";

const chrome = { ...devices["Desktop Chrome"], channel: "chrome" as const };
const secondary = process.env.E2E_BASE_URL_SECONDARY;

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  // The isolated hardened API has one CPU and these cases share one fixture account.
  workers: 1,
  timeout: 60000,
  expect: { timeout: 15000 },
  reporter: process.env.E2E_REPORT_FILE ? [["list"], ["json", { outputFile: process.env.E2E_REPORT_FILE }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000",
    trace: "retain-on-failure",
  },
  projects: secondary ? [{
    name: "chrome",
    testIgnore: /security-events\.spec\.ts/,
    use: chrome,
  }, {
    name: "chrome-security",
    testMatch: /security-events\.spec\.ts/,
    use: { ...chrome, baseURL: secondary },
  }] : [{ name: "chrome", use: chrome }],
  // Reuses an operator-started application; no downloads, mocked APIs or system changes.
});
