import Link from "next/link";
import { ArrowLeft, BrainCircuit } from "lucide-react";
import { AdminConsole } from "@/components/admin-console";

export default function AdminPage() {
  return (
    <main className="admin-shell">
      <header className="admin-header">
        <div>
          <span className="admin-kicker"><BrainCircuit size={15} /> REVIEWED AI WORKFLOW</span>
          <h1>情报处理台</h1>
          <p>AI 只生成草稿。相关性、OCR、翻译以及基于中文内容的分析与摘要均需人工确认。</p>
        </div>
        <Link href="/"><ArrowLeft size={15} /> 返回消息流</Link>
      </header>
      <AdminConsole />
    </main>
  );
}
