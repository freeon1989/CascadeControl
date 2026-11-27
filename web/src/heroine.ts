const KNOWN = new Set(["ru", "nl", "at", "de", "fi", "us", "fr", "gb", "se", "pl", "kz", "hk", "il", "in"]);

const ALIASES: Record<string, string[]> = {
  ru: ["russia", "piter", "spb", "petersburg", "peterburg", "sankt", "moscow", "moskva", "питер", "спб", "петербург", "москва", "росси", "санкт"],
  nl: ["amsterdam", "netherlands", "holland", "dutch", "nederland", "амстердам", "нидерланд", "голланд"],
  at: ["vena", "vienna", "wien", "austria", "osterreich", "вена", "австри", "виен"],
  de: ["germany", "deutschland", "frankfurt", "berlin", "munich", "falkenstein", "nuremberg", "герман", "франкфурт", "берлин", "мюнхен", "фалькенштайн", "нюрнберг"],
  fi: ["finland", "suomi", "helsinki", "финлянд", "хельсинки"],
  us: ["usa", "united states", "america", "new york", "charlotte", "сша", "америк", "нью-йорк"],
  fr: ["france", "paris", "франци", "париж"],
  gb: ["united kingdom", "england", "britain", "london", "великобритан", "англи", "британ", "лондон"],
  se: ["sweden", "stockholm", "швец", "стокгольм"],
  pl: ["poland", "polska", "warsaw", "warszawa", "польш", "варшав"],
  kz: ["kazakhstan", "almaty", "astana", "казах", "алматы", "астана"],
  hk: ["hong kong", "hongkong", "гонконг"],
  il: ["israel", "tel aviv", "jerusalem", "израил", "тель-авив", "иерусалим"],
  in: ["india", "mumbai", "delhi", "индия", "индии", "мумба", "дели"],
};

export type HeroineItem = { key: string; title: string; src: string; auto: boolean };

export function resolveCountry(...parts: Array<string | null | undefined>): string {
  const blob = parts.map((part) => part || "").join(" ").trim().toLowerCase();
  if (!blob) return "";
  const tokens = blob.split(/[^a-zа-яё0-9]+/i).filter(Boolean);
  for (const tok of tokens) {
    if (KNOWN.has(tok)) return tok;
  }
  for (const [code, words] of Object.entries(ALIASES)) {
    for (const word of words) {
      if (word.length >= 3 && blob.includes(word)) return code;
      if (word.length >= 5 && tokens.some((tok) => tok.length >= 5 && word.startsWith(tok))) return code;
    }
  }
  return "";
}

export function heroineSrc(country?: string | null, name?: string | null, host?: string | null): string {
  const hint = host && !/^(\d{1,3}\.){3}\d{1,3}$/i.test(host.trim()) && !host.includes(":") ? host : "";
  const code = resolveCountry(country, name, hint);
  if (KNOWN.has(code)) return `/heroines/heroine_${code}.webp`;
  return "/heroines/heroine_default.webp";
}
