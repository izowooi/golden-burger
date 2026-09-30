import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { LoadState } from "@/components/ui";
import { loadMarkdown, REPORT_PATH } from "@/lib/storage";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ path: string[] }> };

function relPath(segments: string[]) {
  const rel = segments.map((s) => decodeURIComponent(s)).join("/").replace(/\.md$/, "");
  return REPORT_PATH.test(rel) ? rel : null;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { path } = await params;
  return { title: relPath(path) ?? "리포트" };
}

export default async function ReportPage({ params }: Props) {
  const { path } = await params;
  const rel = relPath(path);
  if (!rel) notFound();
  const res = await loadMarkdown(`reports/${rel}.md`);
  return (
    <>
      <p className="sub" style={{ margin: "16px 0 0" }}><Link href="/reports">← 리포트 목록</Link></p>
      {res.state !== "ok" ? (
        <div className="section"><LoadState result={res} /></div>
      ) : (
        <article className="card markdown section">
          <Markdown remarkPlugins={[remarkGfm]}>{res.data}</Markdown>
        </article>
      )}
    </>
  );
}
