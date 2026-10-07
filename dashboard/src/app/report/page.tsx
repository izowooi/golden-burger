import { Suspense } from "react";

import { Loading } from "@/components/ui";

import ReportView from "./view";

export default function Page() {
  return <Suspense fallback={<Loading />}><ReportView /></Suspense>;
}
