export function EmptyState({ icon: Icon, title, description, action }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-line bg-surface/60 px-6 py-16 text-center">
      {Icon && (
        <span className="focus-ring mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-brand-light text-brand">
          <Icon width={22} height={22} />
        </span>
      )}
      <p className="font-display text-base font-bold text-ink">{title}</p>
      {description && <p className="mt-1.5 max-w-sm text-sm leading-relaxed text-muted">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
