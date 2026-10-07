import type { Metadata } from "next";
import { Suspense } from "react";

import { Loading } from "@/components/ui";

import Ou05View from "./view";

export const metadata: Metadata = { title: "0.5 Over 추이" };

export default function Page() {
  return <Suspense fallback={<Loading />}><Ou05View /></Suspense>;
}
