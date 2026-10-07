import { Suspense } from "react";

import { Loading } from "@/components/ui";

import StrategyView from "./view";

export default function Page() {
  return <Suspense fallback={<Loading />}><StrategyView /></Suspense>;
}
