/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ["'Space Grotesk'", 'sans-serif'],
        mono: ["'JetBrains Mono'", 'monospace'],
      },
      // Tokens resolve to CSS custom properties (RGB channel triples) so the
      // whole palette is a runtime theme swap. `<alpha-value>` keeps Tailwind
      // opacity modifiers (bg-accent/10, border-good/30) working.
      colors: {
        bg:          'rgb(var(--c-bg) / <alpha-value>)',
        panel:       'rgb(var(--c-panel) / <alpha-value>)',
        'panel-hi':  'rgb(var(--c-panel-hi) / <alpha-value>)',
        border:      'rgb(var(--c-border) / <alpha-value>)',
        'border-hi': 'rgb(var(--c-border-hi) / <alpha-value>)',
        ink:         'rgb(var(--c-ink) / <alpha-value>)',
        dim:         'rgb(var(--c-dim) / <alpha-value>)',
        faint:       'rgb(var(--c-faint) / <alpha-value>)',
        accent:      'rgb(var(--c-accent) / <alpha-value>)',
        'accent-ink':'rgb(var(--c-accent-ink) / <alpha-value>)',
        good:        'rgb(var(--c-good) / <alpha-value>)',
        warn:        'rgb(var(--c-warn) / <alpha-value>)',
        danger:      'rgb(var(--c-danger) / <alpha-value>)',
        grid:        'rgb(var(--c-grid) / <alpha-value>)',
      },
      keyframes: {
        'pulse-soft': { '0%, 100%': { opacity: '1' }, '50%': { opacity: '0.85' } },
        'fade-up':    { from: { opacity: '0', transform: 'translateY(8px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
        blink:        { '0%, 100%': { opacity: '1' }, '50%': { opacity: '0.3' } },
      },
      animation: {
        'pulse-soft': 'pulse-soft 2.4s ease-in-out infinite',
        'fade-up':    'fade-up 0.5s cubic-bezier(0.22,1,0.36,1) forwards',
        blink:        'blink 2.2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
