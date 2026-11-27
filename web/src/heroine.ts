const KNOWN = new Set(["ru", "nl", "at", "de", "fi", "us", "fr", "gb", "se", "pl"]);

export function heroineSrc(country?: string | null): string {
  const code = (country || "").trim().toLowerCase();
  if (KNOWN.has(code)) return `/heroines/heroine_${code}.webp`;
  return "/heroines/heroine_default.webp";
}
