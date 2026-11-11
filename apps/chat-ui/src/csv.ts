// CSV export of a comparison table (Broker Assistant). The table is read from the rendered answer, so the
// export shows exactly what the user saw: the SBC's own wording, nothing recomputed.

export function toCsv(rows: string[][]): string {
  const cell = (value: string): string => {
    const v = value.replace(/\s+/g, " ").trim();
    // Quote when needed; prefix formula-looking cells so a spreadsheet shows them as text (CSV injection).
    const safe = /^[=+\-@\t\r]/.test(v) ? `'${v}` : v;
    return /[",\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
  };
  return rows.map((row) => row.map(cell).join(",")).join("\r\n") + "\r\n";
}

export function tableRows(table: HTMLTableElement): string[][] {
  return Array.from(table.rows, (row) => Array.from(row.cells, (c) => c.textContent ?? ""));
}

export function downloadCsv(filename: string, csv: string): void {
  const url = URL.createObjectURL(new Blob(["﻿", csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
