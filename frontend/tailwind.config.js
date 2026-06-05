/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Barlow', 'sans-serif'],
        mono: ['IBM Plex Mono', 'monospace'],
      },
      colors: {
        bg:       '#070b14',
        panel:    'rgba(9,15,29,0.66)',
        'panel-hi':'rgba(11,19,37,0.78)',
        border:   'rgba(255,255,255,0.055)',
        'border-hi':'rgba(255,255,255,0.10)',
        ink:      '#e9f1ff',
        dim:      '#a9bbd9',
        faint:    '#6c82a8',
        accent:   '#2563eb',
        'accent-ink':'#9cc4fd',
        good:     '#3ee0a5',
        warn:     '#fbbf24',
        danger:   '#f87171',
        grid:     'rgba(255,255,255,0.03)',
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
