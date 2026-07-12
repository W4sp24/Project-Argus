"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AXIS_TICK, TOOLTIP_STYLE } from "./chartTheme";

interface LineChartPanelProps {
  data: Record<string, string | number>[];
  series: { key: string; color: string }[];
}

/** Multi-series line chart used for per-course practice-exam score trends. */
export default function LineChartPanel({ data, series }: LineChartPanelProps) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 8, right: 8, left: -22, bottom: 0 }}>
        <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
        <XAxis dataKey="day" tick={AXIS_TICK} tickLine={false} axisLine={false} />
        <YAxis domain={[0, 100]} unit="%" tick={AXIS_TICK} tickLine={false} axisLine={false} />
        <Tooltip contentStyle={TOOLTIP_STYLE} />
        {series.map(({ key, color }) => (
          <Line
            key={key}
            dataKey={key}
            stroke={color}
            strokeWidth={2}
            dot={{ r: 4, strokeWidth: 0, fill: color }}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
