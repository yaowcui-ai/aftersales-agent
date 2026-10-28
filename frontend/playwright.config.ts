import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60000,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    actionTimeout: 15000,
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:5173",
    trace: "off",
    screenshot: "only-on-failure",
    ...devices["Desktop Edge"],
    channel: process.env.CI ? "chromium" : "msedge",
  },
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: "npm run dev -- --host 127.0.0.1",
        url: "http://127.0.0.1:5173",
        reuseExistingServer: true,
        timeout: 30000,
      },
});
