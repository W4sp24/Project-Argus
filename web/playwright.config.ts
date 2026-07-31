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
  // The list reporter writes nothing to disk, so CI's "upload playwright-report
  // on failure" step had never once produced an artifact -- a failed e2e run
  // could only be read by someone logged in with access to the raw job log.
  // The HTML report is what makes a CI failure diagnosable at all.
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
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
