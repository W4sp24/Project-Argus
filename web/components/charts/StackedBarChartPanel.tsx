"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AXIS_TICK, SERIES, SURFACE, TOOLTIP_STYLE } from "./chartTheme";

interface StackedBarChartPanelProps {
  data: { day: string; events: number; focus: number }[];
}

/** Two-series stacked bar chart used for calendar load vs focus time. */
export default function StackedBarChartPanel({ data }: StackedBarChartPanelProps) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 8, right: 4, left: -22, bottom: 0 }}>
        <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
        <XAxis dataKey="day" tick={AXIS_TICK} tickLine={false} axisLine={false} />
        <YAxis unit="h" tick={AXIS_TICK} tickLine={false} axisLine={false} />
        <Tooltip cursor={{ fill: "rgba(139,92,246,0.08)" }} contentStyle={TOOLTIP_STYLE} />
        <Bar
          dataKey="events"
          stackId="day"
          fill={SERIES.violet}
          stroke={SURFACE}
          strokeWidth={2}
          maxBarSize={22}
        />
        <Bar
          dataKey="focus"
          stackId="day"
          fill={SERIES.cyan}
          stroke={SURFACE}
          strokeWidth={2}
          radius={[4, 4, 0, 0]}
          maxBarSize={22}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}
