/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "class",
  content: [
    "./app/**/*.{js,ts,jsx,tsx}",
    "./components/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: { 50: "#f0fdfa", 100: "#ccfbf1", 500: "#10b981", 600: "#059669", 900: "#064e3b" },
      },
    },
  },
  plugins: [],
}
