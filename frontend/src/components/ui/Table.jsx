export function Table({ columns, rows, keyField = "id", emptyLabel = "Aucune donnée" }) {
  if (!rows || rows.length === 0) {
    return <p className="py-8 text-center text-sm text-muted">{emptyLabel}</p>;
  }

  return (
    <div className="-mx-2 overflow-x-auto">
      <table className="w-full min-w-[560px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-line text-left">
            {columns.map((col) => (
              <th
                key={col.key}
                className="whitespace-nowrap px-2.5 py-2.5 text-xs font-semibold uppercase tracking-wide text-muted"
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row[keyField]} className="border-b border-line/70 last:border-0 hover:bg-paper/70">
              {columns.map((col) => (
                <td key={col.key} className="whitespace-nowrap px-2.5 py-3 text-ink">
                  {col.render ? col.render(row) : (row[col.key] ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
