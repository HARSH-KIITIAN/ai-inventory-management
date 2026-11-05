import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend runs on :8000; proxy API calls there in dev so the UI can use
// same-origin relative paths (/api/...).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
