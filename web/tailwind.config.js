/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "rgb(var(--ink) / <alpha-value>)",
        panel: "rgb(var(--panel) / <alpha-value>)",
        card: "rgb(var(--card) / <alpha-value>)",
        line: "rgb(var(--line) / <alpha-value>)",
        mist: "rgb(var(--mist) / <alpha-value>)",
        foam: "rgb(var(--foam) / <alpha-value>)",
        teal: "rgb(var(--teal) / <alpha-value>)",
        tide: "rgb(var(--tide) / <alpha-value>)",
        amber: "rgb(var(--amber) / <alpha-value>)",
        iris: "rgb(var(--iris) / <alpha-value>)",
        rose: "rgb(var(--rose) / <alpha-value>)",
        ok: "rgb(var(--ok) / <alpha-value>)",
      },
      fontFamily: {
        sans: ["Manrope", "Segoe UI", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
      },
      boxShadow: {
        glow: "0 0 0 1px rgb(var(--teal) / 0.35), 0 0 32px rgb(var(--teal) / 0.12)",
      },
    },
  },
  plugins: [],
};
