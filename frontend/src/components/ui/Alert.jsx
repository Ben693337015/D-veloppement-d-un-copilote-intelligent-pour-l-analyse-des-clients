import { IconAlert, IconCheck } from "../icons";

const STYLES = {
  success: "bg-success-bg text-success",
  danger: "bg-danger-bg text-danger",
  warning: "bg-warning-bg text-accent-dark",
  info: "bg-brand-light text-brand",
};

export function Alert({ tone = "info", children, className = "" }) {
  const Icon = tone === "success" ? IconCheck : IconAlert;
  return (
    <div className={`flex items-start gap-2.5 rounded-xl px-4 py-3 text-sm ${STYLES[tone]} ${className}`}>
      <Icon className="mt-0.5 shrink-0" />
      <div className="leading-relaxed">{children}</div>
    </div>
  );
}
