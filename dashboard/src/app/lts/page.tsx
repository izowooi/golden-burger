import type { Metadata } from "next";
import { Suspense } from "react";

import { Loading } from "@/components/ui";

import LtsView from "./view";

export const metadata: Metadata = { title: "후반 안정성" };

export default function Page() {
  return <Suspense fallback={<Loading />}><LtsView /></Suspense>;
}
