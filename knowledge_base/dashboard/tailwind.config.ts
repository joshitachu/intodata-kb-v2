import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Massilia", "Segoe UI", "system-ui", "sans-serif"],
        mono: ["DM Mono", "ui-monospace", "monospace"],
      },
      colors: {
        surface: {
          bg: "#060e1e",
          card: "#0d1a2e",
          raised: "#132240",
          deep: "#081425",
        },
        "border-dim": "rgba(255,255,255,0.07)",
        accent: {
          primary: "#fe9543",
          secondary: "#6370b0",
          tertiary: "#a0e06f",
        },
        brand: {
          navy: "#001e4d",
          light: "#ebebed",
          orange: "#fe9543",
          purple: "#6370b0",
          green: "#a0e06f",
        },
        platform: {
          talend: "#4285F4",
          workato: "#F97316",
          generic: "#64748B",
        },
      },
      boxShadow: {
        card: "0 2px 8px rgba(0,0,0,0.4), inset 0 0 0 1px rgba(255,255,255,0.07)",
        "card-hover":
          "0 4px 16px rgba(254,149,67,0.12), inset 0 0 0 1px rgba(254,149,67,0.25)",
      },
      borderRadius: {
        card: "4px",
      },
      keyframes: {
        "slide-up": {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "pulse-dot": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.4" },
        },
        "fade-in": {
          "0%": { opacity: "0", transform: "scale(0.98)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
      },
      animation: {
        "slide-up": "slide-up 200ms ease-out",
        "pulse-dot": "pulse-dot 2s ease-in-out infinite",
        "fade-in": "fade-in 150ms ease-out",
      },
    },
  },
  plugins: [],
} satisfies Config;
