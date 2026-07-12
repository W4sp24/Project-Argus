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
import { AXIS_TICK, TOOLTIP_STYLE } from "./chartTheme";

interface BarChartPanelProps {
  data: Record<string, string | number>[];
  dataKey: string;
  color: string;
  unit?: string;
}

/** Single-series bar chart used across Insights (coding sessions, tasks completed, overdue). */
export default function BarChartPanel({ data, dataKey, color, unit }: BarChartPanelProps) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 8, right: 4, left: -22, bottom: 0 }}>
        <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
        <XAxis
          dataKey="day"
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={{ stroke: "rgba(255,255,255,0.1)" }}
          interval={1}
        />
        <YAxis
          allowDecimals={false}
          unit={unit}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={false}
        />
        <Tooltip cursor={{ fill: `${color}14` }} contentStyle={TOOLTIP_STYLE} />
        <Bar dataKey={dataKey} fill={color} radius={[4, 4, 0, 0]} maxBarSize={18} />
      </BarChart>
    </ResponsiveContainer>
  );
}
