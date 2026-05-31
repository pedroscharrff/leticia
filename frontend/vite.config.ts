import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/auth": "http://localhost:8000",
      "/admin": "http://localhost:8000",
      "/webhook": "http://localhost:8000",
      "/billing": "http://localhost:8000",
      "/signup": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "/metrics": "http://localhost:8000",
      "/portal": {
        target: "http://localhost:8000",
        bypass(req) {
          const accept = req.headers["accept"] ?? "";
          if (accept.includes("text/html")) return req.url;
          return null;
        },
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
