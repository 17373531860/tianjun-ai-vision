/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'ind-bg': '#0f172a',
        'ind-panel': '#1e293b',
        'industrial-accent': '#3b82f6',
        'tech-blue': '#00a8ff',
        'status-ok': '#10b981', 
        'status-warn': '#f59e0b',
        'status-ng': '#ef4444',
      }
    },
  },
  plugins: [],
}
