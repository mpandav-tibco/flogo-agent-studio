/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#0d1129",
          100: "#151b3a",
          500: "#4f6ef7",
          600: "#3b56e8",
          700: "#2d43cc",
        },
      },
    },
  },
  plugins: [],
};
