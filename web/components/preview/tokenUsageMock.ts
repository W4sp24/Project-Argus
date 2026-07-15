/**
 * TOKENS.CLAUDE [PREVIEW] mock data (§14, §8 flags.tokenUsage). Kept tiny —
 * static numbers only, no network calls — until `backend/agent/` logs real
 * `{ts, feature, session_id, input_tokens, output_tokens}` rows and
 * `GET /api/usage` exists. Grep guard (§8): this file must stay free of any
 * data-fetching call.
 */

export interface TokenView {
  totalTokens: number;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  capTokens: number;
  chart: number[];
  axisLabels: string[];
  features: { name: string; tokens: number }[];
  rangeLabel: string;
}

export const TOKEN_USAGE_MOCK: { session: TokenView; week: TokenView; all: TokenView } = {
  session: {
    totalTokens: 18_420,
    inputTokens: 12_050,
    outputTokens: 6_370,
    costUsd: 0.31,
    capTokens: 25_000,
    chart: [800, 1400, 1200, 2100, 1800, 2600, 2200, 3000, 1900, 1420],
    axisLabels: ["1", "3", "5", "7", "9"],
    features: [
      { name: "chat", tokens: 9_800 },
      { name: "briefing", tokens: 4_200 },
      { name: "planner", tokens: 3_100 },
      { name: "study", tokens: 1_320 },
    ],
    rangeLabel: "since session start",
  },
  week: {
    totalTokens: 96_500,
    inputTokens: 61_200,
    outputTokens: 35_300,
    costUsd: 1.62,
    capTokens: 175_000,
    chart: [9_800, 14_200, 11_600, 16_900, 13_400, 18_200, 12_400],
    axisLabels: ["Mon 07", "Tue 08", "Wed 09", "Thu 10", "Fri 11", "Sat 12", "Sun 13"],
    features: [
      { name: "chat", tokens: 48_600 },
      { name: "briefing", tokens: 21_400 },
      { name: "planner", tokens: 17_800 },
      { name: "study", tokens: 8_700 },
    ],
    rangeLabel: "7-day total",
  },
  all: {
    totalTokens: 1_284_000,
    inputTokens: 812_000,
    outputTokens: 472_000,
    costUsd: 21.4,
    capTokens: 2_000_000,
    chart: [142_000, 168_000, 155_000, 190_000, 176_000, 210_000, 243_000],
    axisLabels: ["w23", "w24", "w25", "w26", "w27", "w28", "w29"],
    features: [
      { name: "chat", tokens: 640_000 },
      { name: "briefing", tokens: 298_000 },
      { name: "planner", tokens: 231_000 },
      { name: "study", tokens: 115_000 },
    ],
    rangeLabel: "since 2026-04-01 · 105 days",
  },
};
