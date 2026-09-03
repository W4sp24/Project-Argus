import path from "node:path";
import { defineConfig } from "vitest/config";

/**
 * Node environment, no DOM. Everything unit-tested here is pure logic --
 * answer matching, distractor selection, delimited parsing, job
 * reconciliation. Components are covered by Playwright against a real
 * backend, which is the only place their SWR and WebSocket behaviour is real,
 * and adding a DOM harness here would buy a worse version of that.
 *
 * `include` is scoped to lib/ for the same reason: a test file that needs a
 * document belongs in the Playwright suite, and this config cannot run one.
 */
export default defineConfig({
  resolve: { alias: { "@": path.resolve(__dirname) } },
  test: {
    environment: "node",
    include: ["lib/**/*.test.ts"],
  },
});
