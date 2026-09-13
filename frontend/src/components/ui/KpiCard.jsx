export function KpiCard({ icon: Icon, label, value, hint, tone = "brand" }) {
  const toneClasses = {
    brand: "bg-brand-light text-brand",
    accent: "bg-accent-light text-accent-dark",
    danger: "bg-danger-bg text-danger",
    success: "bg-success-bg text-success",
  };

  return (
    <div className="min-w-0 rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow-card)]">
      <div className="flex items-start justify-between gap-2">
        <p className="truncate text-[13px] font-medium text-muted">{label}</p>
        {Icon && (
          <span className={`focus-ring flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${toneClasses[tone]}`}>
            <Icon width={16} height={16} />
          </span>
        )}
      </div>
      <p
        className="font-tabular mt-2 truncate text-[22px] font-semibold leading-tight text-ink sm:text-[26px]"
        title={hint || undefined}
      >
        {value}
      </p>
      {hint && <p className="mt-1 truncate text-xs text-muted" title={hint}>{hint}</p>}
    </div>
  );
}
