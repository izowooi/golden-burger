import type { Metadata } from "next";
import { Suspense } from "react";

import { Loading } from "@/components/ui";

import ExploreView from "./view";

export const metadata: Metadata = { title: "시각화" };

export default function Page() {
  return <Suspense fallback={<Loading />}><ExploreView /></Suspense>;
}
