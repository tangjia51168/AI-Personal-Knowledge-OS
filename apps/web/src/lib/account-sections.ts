// Only these non-secret fragments are navigation, never mail recovery tokens.
export const accountSections = ["overview", "security", "jobs", "inbox", "collections", "knowledge", "research", "exports", "learning", "files", "models", "audit"] as const;
export function isAccountSection(fragment: string) {
  return accountSections.some(section => fragment === `#${section}`);
}
