interface StatsCardProps {
  label: string;
  value: number | string;
  highlight?: boolean;
}

export default function StatsCard({ label, value, highlight }: StatsCardProps) {
  return (
    <div
      className={`rounded-lg border p-4 ${
        highlight ? "border-indigo-700 bg-indigo-950" : "border-gray-700 bg-gray-900"
      }`}
    >
      <div className="text-xs text-gray-400 uppercase tracking-wide mb-1">{label}</div>
      <div className={`text-3xl font-bold ${highlight ? "text-indigo-300" : "text-white"}`}>
        {value}
      </div>
    </div>
  );
}
