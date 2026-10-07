"use client";

import Link from "next/link";
import { useEffect } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Loading, LoadState } from "@/components/ui";
import { REPORT_PATH, useMarkdown, usePathSegments } from "@/lib/storage";

function relPath(rest: string) {
  const rel = rest.split("/").map((s) => decodeURIComponent(s)).join("/").replace(/\.md$/, "");
  return REPORT_PATH.test(rel) ? rel : null;
}

/** Served for /reports/<kind>/<name> (Slack links) by worker/index.ts. */
export default function ReportView() {
  const rest = usePathSegments("reports");
  const rel = rest ? relPath(rest) : null;
  const res = useMarkdown(rel ? `reports/${rel}.md` : null);
  useEffect(() => { if (rel) document.title = `${rel} · Polylab`; }, [rel]);
  if (rest === undefined) return <Loading />;
  if (!rel) return <><h1>찾을 수 없음</h1><p className="sub">리포트 경로가 올바르지 않습니다. <Link href="/reports">리포트 목록</Link></p></>;
  if (!res) return <Loading />;
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
