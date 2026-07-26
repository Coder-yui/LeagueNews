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
          <p>AI 只生成草稿。相关性、单条分析、事件变更和报告均由人工确认后生效。</p>
        </div>
        <Link href="/"><ArrowLeft size={15} /> 返回事件流</Link>
      </header>
      <AdminConsole />
    </main>
  );
}

