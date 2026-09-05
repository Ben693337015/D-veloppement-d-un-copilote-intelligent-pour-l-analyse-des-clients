export function Select({ label, required, ...props }) {
  return (
    <label className="flex flex-col gap-1.5">
      {label && (
        <span className="text-[13px] font-medium text-ink">
          {label}
          {required && <span className="ml-0.5 text-danger">*</span>}
        </span>
      )}
      <select
        className="w-full appearance-none rounded-xl border border-line bg-surface px-3.5 py-2.5 text-sm text-ink outline-none transition-colors focus:border-brand"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%235b6478' stroke-width='2' stroke-linecap='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")",
          backgroundRepeat: "no-repeat",
          backgroundPosition: "right 12px center",
          paddingRight: "2.25rem",
        }}
        {...props}
      />
    </label>
  );
}
