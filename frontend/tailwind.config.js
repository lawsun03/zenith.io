/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ["'Space Grotesk'", 'sans-serif'],
        mono: ["'JetBrains Mono'", 'monospace'],
      },
      colors: {
        bg:          '#070c1a',
        panel:       '#0d1628',
        'panel-hi':  '#131e35',
        border:      '#1d2a42',
        'border-hi': '#2c3e5c',
        ink:         '#e8f0ff',
        dim:         '#6a85b0',
        faint:       '#3d5070',
        accent:      '#6ee7b7',
        'accent-ink':'#a7f3d0',
        good:        '#6ee7b7',
        warn:        '#fbbf24',
        danger:      '#fca5a5',
        grid:        'rgba(255,255,255,0.03)',
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
