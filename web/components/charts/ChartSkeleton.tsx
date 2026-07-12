/** Placeholder shown while a recharts panel's chunk is still downloading. */
export default function ChartSkeleton() {
  return (
    <div className="flex h-full w-full animate-pulse items-end gap-1.5 px-1 pb-1">
      {[40, 65, 30, 80, 50, 70, 45].map((height, i) => (
        <div
          key={i}
          className="flex-1 rounded-t bg-white/[0.06]"
          style={{ height: `${height}%` }}
        />
      ))}
    </div>
  );
}
