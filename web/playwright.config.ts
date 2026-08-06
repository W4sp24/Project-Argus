import { defineConfig } from "@playwright/test";

/** Stub n8n port. Deliberately not 5679: a real local n8n already binds it. */
const STUB_N8N_PORT = Number(process.env.STUB_N8N_PORT || 5799);

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
  // The html report fixes that; the github reporter turns each failure into a
  // run-summary annotation, which is readable *without* signing in at all --
  // the same reasoning as desktop/tests/smoke_backend.py's annotate().
  reporter: process.env.CI
    ? [["github"], ["list"], ["html", { open: "never" }]]
    : [["list"]],
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
    // The stub n8n (e2e/stub-n8n.mjs) — a third, independent webServer entry
    // rather than a child process spawned from inside start-backend.mjs.
    // start-backend.mjs's job is provisioning the throwaway vault and
    // launching uvicorn; bolting a second, unrelated process onto it would
    // mean hand-rolling the "kill it too, even if the backend crashes first"
    // bookkeeping that Playwright's webServer array already does for every
    // entry uniformly. Using this array instead means the stub is started,
    // health-checked and torn down by the same mechanism as the other two
    // servers, with no extra code — including the case that matters most
    // here, a crash mid-run not leaking a listening port into the next one.
    // No dedicated health route exists on the stub (it only speaks n8n's own
    // API shape), so `port` is used instead of `url`: Playwright just waits
    // for something to accept a TCP connection on it, which is all "is the
    // stub up" needs to mean here.
    // Port is env-overridable and defaults OFF n8n's own range. n8n binds
    // 5678 *and* 5679 (its task-runner broker), so hardcoding 5679 meant this
    // suite could not run on a machine with n8n running — precisely the
    // machine that develops this feature. STUB_N8N_PORT keeps it steerable.
    {
      command: "node e2e/stub-n8n.mjs",
      port: STUB_N8N_PORT,
      reuseExistingServer: false,
      timeout: 15_000,
      env: { STUB_N8N_PORT: String(STUB_N8N_PORT) },
    },
    // A production build, not `next dev`. The dev server compiles routes on
    // demand and carries the webpack compiler and HMR runtime in-process, and
    // it dies partway through a full run: one test hangs for ~16s and every
    // test after it fails in a uniform ~3.2s with ERR_CONNECTION_REFUSED, with
    // the death point moving between runs. That made the suite unusable as a
    // gate no matter how many specs were correct. A built server also hydrates
    // without on-demand compilation (the race that made email capture flaky)
    // and enforces the stricter production CSP -- no 'unsafe-eval' -- which is
    // what actually ships. Costs one build per run.
    {
      command: "npm run build && npm run start -- -p 3100",
      url: "http://127.0.0.1:3100",
      reuseExistingServer: false,
      timeout: 300_000,
    },
  ],
});
