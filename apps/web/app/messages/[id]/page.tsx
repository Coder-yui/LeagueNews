import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { MessageDetail } from "@/components/message-detail";
import { getPublishedItem } from "@/lib/api";

export default async function MessagePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const itemId = Number(id);
  if (!Number.isInteger(itemId) || itemId < 1) notFound();
  const item = await getPublishedItem(itemId);
  if (!item) notFound();

  return (
    <main className="message-page">
      <header className="site-header">
        <Link className="brand" href="/">
          <span className="brand-mark">LD</span>
          <span>LoL Daily Intel</span>
        </Link>
        <nav aria-label="主要导航">
          <Link className="active" href="/">消息</Link>
          <Link href="/events">事件</Link>
          <Link href="/admin">处理台</Link>
        </nav>
        <div className="live-state"><span /> Reviewed</div>
      </header>
      <Link className="message-back" href="/">
        <ArrowLeft size={15} /> 返回消息列表
      </Link>
      <MessageDetail item={item} />
      <footer><span>LoL Daily Intel · Reviewed message</span><span>Raw → AI → Human review</span></footer>
    </main>
  );
}
