import "./MetricCard.css";

interface MetricCardProps {
  label: string;
  value: string | number;
  sub?: string;
  accent?: boolean;
  icon?: React.ReactNode;
}

export function MetricCard({ label, value, sub, accent, icon }: MetricCardProps) {
  return (
    <div className={`metric-card ${accent ? "metric-card--accent" : ""}`}>
      {icon && <div className="metric-card__icon">{icon}</div>}
      <p className="metric-card__label">{label}</p>
      <p className="metric-card__value">{value}</p>
      {sub && <p className="metric-card__sub">{sub}</p>}
    </div>
  );
}
