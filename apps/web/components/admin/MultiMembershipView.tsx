"use client";

import Link from "next/link";
import type { EventDetail } from "@/lib/types";

type MembershipRow = { itemId: number; title: string; primary: Array<{ id: number; title: string; type: string }>; component: Array<{ id: number; title: string; type: string }> };

export function MultiMembershipView({ events, onRemove }: { events: EventDetail[]; onRemove?: (eventId: number, itemId: number) => void }) {
  const rows = new Map<number, MembershipRow>();
  for (const event of events) for (const message of event.messages) {
    const row = rows.get(message.normalized_item_id) ?? { itemId: message.normalized_item_id, title: message.title, primary: [], component: [] };
    const target = message.membership_role === "component" ? row.component : row.primary;
    target.push({ id: event.id, title: event.title, type: event.event_kind }); rows.set(message.normalized_item_id, row);
  }
  const visible = [...rows.values()].filter((row) => row.component.length > 0);
  if (!visible.length) return <div className="admin-empty">当前结果中没有 component 多归属消息。</div>;
  return <div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>消息</th><th>事件 A（primary）</th><th>事件 B（component）</th></tr></thead><tbody>{visible.map((row) => <tr key={row.itemId}><td><Link href={`/admin/messages/${row.itemId}`}>{row.title}</Link></td><td>{row.primary.map((event) => <Link className="admin-membership-cell" href={`/admin/events/${event.id}`} key={event.id}><span>{event.type}</span>{event.title}</Link>)}</td><td>{row.component.map((event) => <div className="admin-membership-cell" key={event.id}><Link href={`/admin/events/${event.id}`}><span>{event.type}</span>{event.title}</Link>{onRemove && <button type="button" onClick={() => onRemove(event.id, row.itemId)}>解除关联</button>}</div>)}</td></tr>)}</tbody></table></div>;
}
