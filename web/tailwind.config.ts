import type { Config } from "tailwindcss"

export default {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
        card: "var(--card)",
        "card-foreground": "var(--card-foreground)",
        primary: "var(--primary)",
        "primary-foreground": "var(--primary-foreground)",
        secondary: "var(--secondary)",
        "secondary-foreground": "var(--secondary-foreground)",
        muted: "var(--muted)",
        "muted-foreground": "var(--muted-foreground)",
        faint: "var(--faint)",
        accent: "var(--accent)",
        "accent-foreground": "var(--accent-foreground)",
        "accent-soft": "var(--accent-soft)",
        warning: "var(--warning)",
        "warning-soft": "var(--warning-soft)",
        destructive: "var(--destructive)",
        "destructive-soft": "var(--destructive-soft)",
        border: "var(--border)",
        input: "var(--input)",
        ring: "var(--ring)",
        stage: {
          DEFAULT: "var(--stage)",
          2: "var(--stage-2)",
          line: "var(--stage-line)",
          ink: "var(--stage-ink)",
          muted: "var(--stage-muted)",
        },
      },
      fontFamily: {
        sans: ["var(--font-body)", "ui-sans-serif", "system-ui", "sans-serif"],
        display: ["var(--font-display)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "var(--radius-md)",
        sm: "var(--radius-sm)",
        xs: "var(--radius-xs)",
      },
      boxShadow: {
        soft: "var(--shadow)",
      },
      maxWidth: {
        shell: "1440px",
      },
      transitionTimingFunction: {
        soft: "cubic-bezier(0.22, 1, 0.36, 1)",
      },
      keyframes: {
        shimmer: {
          to: { backgroundPosition: "-200% 0" },
        },
        spin: {
          to: { transform: "rotate(360deg)" },
        },
        scan: {
          from: { left: "-30%" },
          to: { left: "100%" },
        },
      },
      animation: {
        shimmer: "shimmer 1.2s linear infinite",
        spin: "spin 0.7s linear infinite",
        scan: "scan 1.1s linear infinite",
      },
    },
  },
  plugins: [],
} satisfies Config
