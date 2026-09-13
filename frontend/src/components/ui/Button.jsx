import { Spinner } from "./Spinner";

const VARIANTS = {
  primary: "bg-brand text-white hover:bg-brand-dark disabled:bg-brand/50",
  secondary: "bg-brand-light text-brand hover:bg-brand/15 disabled:opacity-50",
  ghost: "bg-transparent text-ink hover:bg-ink/5 disabled:opacity-50",
  danger: "bg-danger text-white hover:opacity-90 disabled:opacity-50",
};

export function Button({
  children,
  variant = "primary",
  loading = false,
  disabled = false,
  type = "button",
  className = "",
  ...props
}) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-xl px-4 py-2.5 text-sm font-semibold transition-colors disabled:cursor-not-allowed ${VARIANTS[variant]} ${className}`}
      {...props}
    >
      {loading && <Spinner size={14} className="text-current" />}
      {children}
    </button>
  );
}
