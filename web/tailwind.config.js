/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#071018",
        panel: "#0c1728",
        card: "#101c30",
        line: "#21344f",
        mist: "#93a4bf",
        foam: "#e7eefb",
        teal: "#2ee6c7",
        tide: "#14b8a6",
        amber: "#ff9f43",
        iris: "#8b6cff",
        rose: "#ff5d73",
        ok: "#3ee0a2",
      },
      fontFamily: {
        sans: ["Manrope", "Segoe UI", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(46,230,199,.35), 0 0 32px rgba(46,230,199,.12)",
      },
    },
  },
  plugins: [],
};
