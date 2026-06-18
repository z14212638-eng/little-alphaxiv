import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api -> backend FastAPI on :8000 (the CORS proxy).
// In production you'd serve frontend + backend from the same origin.
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
