/** Remote Impact design system (shadcn-style tokens on Tailwind 3).
 *  Tokens live as CSS variables in ui.css; utilities like bg-primary,
 *  text-muted-foreground and border-border resolve to them.
 *  Build: npm run build  (writes ../static/tailwind.min.css)
 */
const hsl = (name) => `hsl(var(--${name}) / <alpha-value>)`;

module.exports = {
  content: [
    "../templates/**/*.html",
    "../jobs/templates/**/*.html",
    "../gigs/templates/**/*.html",
    "../jobs/**/*.py",
    "../gigs/**/*.py",
    "../blog/**/*.py",
    "../jobboard/**/*.py",
  ],
  // Tone classes are chosen in Python (job_extras.area_tone / org_tone), so the scanner
  // can't see them as literal strings.
  safelist: [{ pattern: /^tone-(green|amber|indigo|rose|orange|sky|violet|red|teal|stone)$/ }],
  theme: {
    container: { center: true, padding: { DEFAULT: "1rem", sm: "1.5rem", lg: "2rem" }, screens: { "2xl": "1200px" } },
    extend: {
      // One readable scale for public pages, tools and staff workspaces.
      fontSize: {
        xs: ['0.875rem', { lineHeight: '1.5' }],
        sm: ['0.9375rem', { lineHeight: '1.6' }],
        base: ['1rem', { lineHeight: '1.65' }],
      },
      fontFamily: {
        sans: ["Geist", "Inter", "system-ui", "-apple-system", "sans-serif"],
        display: ["Geist", "Inter", "system-ui", "sans-serif"],
        mono: ["Geist Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      colors: {
        border: hsl("border"),
        input: hsl("input"),
        ring: hsl("ring"),
        background: hsl("background"),
        foreground: hsl("foreground"),
        primary: { DEFAULT: hsl("primary"), foreground: hsl("primary-foreground") },
        secondary: { DEFAULT: hsl("secondary"), foreground: hsl("secondary-foreground") },
        muted: { DEFAULT: hsl("muted"), foreground: hsl("muted-foreground") },
        accent: { DEFAULT: hsl("accent"), foreground: hsl("accent-foreground") },
        destructive: { DEFAULT: hsl("destructive"), foreground: hsl("destructive-foreground") },
        success: { DEFAULT: hsl("success"), foreground: hsl("success-foreground") },
        warning: { DEFAULT: hsl("warning"), foreground: hsl("warning-foreground") },
        card: { DEFAULT: hsl("card"), foreground: hsl("card-foreground") },
        brand: {
          DEFAULT: hsl("brand"),
          50: "#f5ffe8", 100: "#e8ffc9", 200: "#d3ff9a", 300: "#8ad425", 400: "#7bc420",
          500: "#6eaa1c", 600: "#5a8b17", 700: "#517e15", 800: "#3f6212", 900: "#365c10",
        },
        popover: { DEFAULT: hsl("popover"), foreground: hsl("popover-foreground") },
        neon: "#5a8b17",
        twilight: { 800: "#f1ede6", 900: "#f7f4ef" },
        "off-white": "#f7f4ef",
      },
      // Legacy dark-theme names: as text, "twilight" always meant dark ink.
      textColor: { twilight: { 800: "#1e293b", 900: "#0f172a" } },
      spacing: { 18: "4.5rem" },
      backgroundImage: {
        "hero-gradient": "radial-gradient(1200px 500px at 10% -10%, rgb(138 212 37 / 0.18), transparent 60%), radial-gradient(900px 400px at 100% 0%, rgb(47 107 115 / 0.10), transparent 60%)",
        "card-gradient": "linear-gradient(180deg, rgb(255 255 255 / 0.9), rgb(255 255 255 / 0.7))",
        grid: "linear-gradient(rgb(15 23 42 / 0.04) 1px, transparent 1px), linear-gradient(90deg, rgb(15 23 42 / 0.04) 1px, transparent 1px)",
      },
      borderRadius: {
        sm: "calc(var(--radius) - 6px)",
        md: "calc(var(--radius) - 3px)",
        lg: "var(--radius)",
        xl: "calc(var(--radius) + 2px)",
        "2xl": "calc(var(--radius) + 6px)",
      },
      boxShadow: {
        soft: "0 0 0 1px rgb(20 20 25 / 0.04), 0 1px 2px rgb(20 20 25 / 0.04), 0 2px 8px -2px rgb(20 20 25 / 0.05)",
        card: "0 0 0 1px rgb(20 20 25 / 0.04), 0 1px 2px rgb(20 20 25 / 0.04)",
        lift: "0 0 0 1px rgb(20 20 25 / 0.05), 0 8px 24px -8px rgb(20 20 25 / 0.12)",
        glow: "none",
      },
    },
  },
  plugins: [require("@tailwindcss/forms")({ strategy: "class" }), require("@tailwindcss/typography")],
};
