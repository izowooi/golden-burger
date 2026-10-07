import { Suspense } from "react";

import { Loading } from "@/components/ui";

import OverviewView from "./view";

export default function OverviewPage() {
  return <Suspense fallback={<Loading />}><OverviewView /></Suspense>;
}
