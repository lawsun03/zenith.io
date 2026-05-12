/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        mono: ['JetBrains Mono', 'monospace'],
        display: ['Space Mono', 'monospace'],
      },
      colors: {
        bg:     '#000000',
        panel:  '#040604',
        border: '#003a00',
        ink:    '#00ff41',
        dim:    '#00aa22',
        accent: '#00ff41',
        warn:   '#ffd700',
        danger: '#ff3333',
        grid:   '#001200',
      },
      keyframes: {
        'pulse-soft': {
          '0%, 100%': { opacity: '1' },
          '50%':      { opacity: '0.85' },
        },
      },
      animation: {
        'pulse-soft': 'pulse-soft 2.4s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
