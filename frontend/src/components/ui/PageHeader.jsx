export function PageHeader({ icon: Icon, eyebrow, title, subtitle, actions }) {
  return (
    <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="flex items-start gap-3.5">
        {Icon && (
          <span className="mt-1 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-light text-brand">
            <Icon width={20} height={20} />
          </span>
        )}
        <div>
          {eyebrow && (
            <p className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-brand">{eyebrow}</p>
          )}
          <h1 className="font-display text-2xl font-extrabold text-ink sm:text-[28px]">{title}</h1>
          {subtitle && <p className="mt-1.5 max-w-2xl text-[15px] leading-relaxed text-muted">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}
