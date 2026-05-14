import "./Badge.css";

type BadgeVariant = "success" | "warning" | "danger" | "neutral" | "primary";

interface BadgeProps {
  variant?: BadgeVariant;
  children: React.ReactNode;
}

export function Badge({ variant = "neutral", children }: BadgeProps) {
  return <span className={`badge badge--${variant}`}>{children}</span>;
}
