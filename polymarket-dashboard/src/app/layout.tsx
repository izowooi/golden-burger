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
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
