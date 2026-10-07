import type { Metadata } from "next";
import { Suspense } from "react";

import { Loading } from "@/components/ui";

import ManualView from "./view";

export const metadata: Metadata = { title: "수동 베팅" };

export default function Page() {
  return <Suspense fallback={<Loading />}><ManualView /></Suspense>;
}
