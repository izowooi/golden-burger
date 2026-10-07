import type { Metadata } from "next";
import { Suspense } from "react";

import { Loading } from "@/components/ui";

import TransactionsView from "./view";

export const metadata: Metadata = { title: "지난 24시간 거래" };

export default function TransactionsPage() {
  return <Suspense fallback={<Loading />}><TransactionsView /></Suspense>;
}
