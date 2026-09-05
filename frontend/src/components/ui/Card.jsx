export function Card({ children, className = "", padded = true }) {
  return (
    <div
      className={`rounded-2xl border border-line bg-surface shadow-[var(--shadow-card)] ${
        padded ? "p-5 sm:p-6" : ""
      } ${className}`}
    >
      {children}
    </div>
  );
}

export function CardTitle({ children, action }) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <h2 className="font-display text-base font-bold text-ink">{children}</h2>
      {action}
    </div>
  );
}
