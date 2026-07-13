import { defineConfig } from "@playwright/test";

/**
 * E2E: real backend (throwaway vault, provisioned by e2e/start-backend.mjs)
 * + next dev on 3100 (rewrites proxy /api to 127.0.0.1:8000).
 * reuseExistingServer stays false so a running dev backend (pointing at a
 * REAL vault) can never be picked up by the tests.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  // All specs share one throwaway vault + backend process; running spec
  // files in parallel workers races writes to the same markdown files.
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:3100",
  },
  webServer: [
    {
      command: "node e2e/start-backend.mjs",
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: false,
      timeout: 90_000,
    },
    {
      command: "npm run dev -- -p 3100",
      url: "http://127.0.0.1:3100",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
