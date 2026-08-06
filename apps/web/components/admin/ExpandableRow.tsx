import type { ReactNode } from "react";

export function ExpandableRow({ row, detail, open, colSpan }: { row: ReactNode; detail: ReactNode; open: boolean; colSpan: number }) {
  return (
    <>
      {row}
      {open && <tr className="admin-expanded-row"><td colSpan={colSpan}>{detail}</td></tr>}
    </>
  );
}
