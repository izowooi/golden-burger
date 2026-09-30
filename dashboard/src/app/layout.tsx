import type { Metadata, Viewport } from "next";
import Link from "next/link";

import { ThemeToggle } from "@/components/theme-toggle";
import { isFixtureMode } from "@/lib/storage";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Polylab", template: "%s · Polylab" },
  description: "Polymarket 스포츠 전략 연구 대시보드",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover" };

const THEME_BOOT = `try{var t=localStorage.getItem("polylab-theme");if(t==="dark"||t==="light")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body>
        {isFixtureMode() && <div className="fixture-banner">FIXTURE 모드 — 로컬 샘플 데이터</div>}
        <header className="topbar">
          <div className="topbar-inner">
            <Link href="/" className="brand">Polylab</Link>
            <nav className="nav">
              <Link href="/">개요</Link>
              <Link href="/research">연구</Link>
              <Link href="/reports">리포트</Link>
            </nav>
            <ThemeToggle />
          </div>
        </header>
        <main className="shell">{children}</main>
      </body>
    </html>
  );
}
