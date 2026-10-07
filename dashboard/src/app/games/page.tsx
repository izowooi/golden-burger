import type { Metadata } from "next";
import { Suspense } from "react";

import { Loading } from "@/components/ui";

import GamesView from "./view";

export const metadata: Metadata = { title: "24h 경기" };

export default function Page() {
  return <Suspense fallback={<Loading />}><GamesView /></Suspense>;
}
