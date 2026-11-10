// Only presentation preferences are persisted. Never store session/tenant data here.
export function subscribeTheme(callback: () => void) {
  const media = matchMedia("(prefers-color-scheme: dark)");
  window.addEventListener("storage", callback);
  window.addEventListener("knowledge-theme", callback);
  media.addEventListener("change", callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener("knowledge-theme", callback);
    media.removeEventListener("change", callback);
  };
}

export function readTheme(): "light" | "dark" {
  try {
    const stored = localStorage.getItem("knowledge-os-theme");
    if (stored === "light" || stored === "dark") return stored;
  } catch { /* Restricted browsers may disable localStorage. */ }
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function toggleTheme() {
  const next = readTheme() === "light" ? "dark" : "light";
  try { localStorage.setItem("knowledge-os-theme", next); } catch { return; }
  window.dispatchEvent(new Event("knowledge-theme"));
}
