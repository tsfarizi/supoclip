import { defineConfig } from "@playwright/test";

// The native stack is managed by run.ps1 — this suite intentionally does NOT
// start a webServer. Prerequisite: backend on :8000 and frontend on :3107.
const baseURL = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:3107";

export default defineConfig({
  testDir: "./specs",
  // Single worker + serial execution: specs share the same dev database user,
  // so cross-test parallelism would corrupt state (share tokens, API keys,
  // uploaded tasks).
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["list"],
    ["html", { open: "never" }],
  ],
  globalSetup: "./global-setup.ts",
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 15000,
    navigationTimeout: 30000,
    // Clipboard access is needed by "Copy share link" / API-key copy on
    // localhost (a secure context); grant it so the app's clipboard path is
    // exercised instead of its execCommand fallback.
    permissions: ["clipboard-read", "clipboard-write"],
  },
});
