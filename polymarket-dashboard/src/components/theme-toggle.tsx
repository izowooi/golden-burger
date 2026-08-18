"use client";

import { useSyncExternalStore } from "react";

type Theme = "light" | "dark";

const STORAGE_KEY = "pb-dashboard-theme";

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribeToTheme, readTheme, () => "light");

  const toggleTheme = () => {
    const nextTheme: Theme = theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = nextTheme;
    window.localStorage.setItem(STORAGE_KEY, nextTheme);
    window.dispatchEvent(new Event("pb-dashboard-theme-change"));
  };

  return (
    <button
      className="theme-toggle"
      type="button"
      aria-label={theme === "light" ? "다크 모드로 전환" : "라이트 모드로 전환"}
      aria-pressed={theme === "dark"}
      onClick={toggleTheme}
    >
      <span aria-hidden="true">{theme === "light" ? "☀" : "☾"}</span>
      <span>{theme === "light" ? "라이트" : "다크"}</span>
    </button>
  );
}

function subscribeToTheme(onStoreChange: () => void) {
  window.addEventListener("pb-dashboard-theme-change", onStoreChange);
  window.addEventListener("storage", onStoreChange);
  return () => {
    window.removeEventListener("pb-dashboard-theme-change", onStoreChange);
    window.removeEventListener("storage", onStoreChange);
  };
}

function readTheme(): Theme {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}
