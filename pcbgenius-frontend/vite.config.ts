import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// V1 frontend dev server — this is the origin the backend CORS allows.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
});
