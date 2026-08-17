// Theme toggle (design §17 UX) — switches the WebUI between the light and
// dark palettes by setting documentElement.dataset.theme. The choice is
// persisted in localStorage so it survives reloads; on first visit it follows
// the OS preference (prefers-color-scheme).

import { useEffect, useState } from "react";

const STORAGE_KEY = "iterate-webui-theme";

export type Theme = "light" | "dark";

function initialTheme(): Theme {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export default function ThemeToggle(): React.JSX.Element {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  const toggle = (): void => {
    setTheme((current) => (current === "dark" ? "light" : "dark"));
  };

  return (
    <button
      type="button"
      className="btn theme-toggle"
      onClick={toggle}
      aria-label={theme === "dark" ? "切换到浅色模式" : "切换到暗色模式"}
      title={theme === "dark" ? "切换到浅色模式" : "切换到暗色模式"}
    >
      {theme === "dark" ? "浅色模式" : "暗色模式"}
    </button>
  );
}
