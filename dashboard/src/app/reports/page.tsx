import type { Metadata } from "next";
import { Suspense } from "react";

import { Loading } from "@/components/ui";

import ReportsView from "./view";

export const metadata: Metadata = { title: "리포트" };

export default function Page() {
  return <Suspense fallback={<Loading />}><ReportsView /></Suspense>;
}
