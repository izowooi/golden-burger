import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Polymarket Strategy Monitor",
  description: "Golden strategy lifecycle, portfolio performance, Jenkins health, and host storage dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem("pb-dashboard-theme");document.documentElement.dataset.theme=t==="dark"?"dark":"light"}catch(e){document.documentElement.dataset.theme="light"}`,
          }}
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
