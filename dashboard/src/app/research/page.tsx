import type { Metadata } from "next";
import { Suspense } from "react";

import { Loading } from "@/components/ui";

import ResearchView from "./view";

export const metadata: Metadata = { title: "연구" };

export default function Page() {
  return <Suspense fallback={<Loading />}><ResearchView /></Suspense>;
}
