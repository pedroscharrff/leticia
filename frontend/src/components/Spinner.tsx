import "./Spinner.css";

export function Spinner({ size = 24 }: { size?: number }) {
  return (
    <span className="spinner" style={{ width: size, height: size }} role="status" aria-label="Carregando">
      <svg viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2.5" strokeDasharray="40" strokeDashoffset="30" strokeLinecap="round"/>
      </svg>
    </span>
  );
}
