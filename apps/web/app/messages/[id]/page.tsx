import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { MessageDetail } from "@/components/message-detail";
import { PublicShell } from "@/components/public-shell";
import { getPublishedItem } from "@/lib/api";

export default async function MessagePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ from?: string; fromLabel?: string }>;
}) {
  const { id } = await params;
  const query = await searchParams;
  const itemId = Number(id);
  if (!Number.isInteger(itemId) || itemId < 1) notFound();
  const item = await getPublishedItem(itemId);
  if (!item) notFound();
  const safeReturn = query.from?.startsWith("/") && !query.from.startsWith("//") ? query.from : "/messages";
  const returnLabel = query.fromLabel?.slice(0, 30) || "返回消息列表";

  return (
    <PublicShell className="message-page">
      <div className="public-frame message-detail-frame">
        <Link className="message-back" href={safeReturn}><ArrowLeft size={15} /> {returnLabel}</Link>
        <MessageDetail item={item} />
      </div>
    </PublicShell>
  );
}
