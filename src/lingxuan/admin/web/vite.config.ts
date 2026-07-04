import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  base: "/admin/",
  build: {
    outDir: "dist",
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    proxy: {
      "/admin/api": {
        target: "http://127.0.0.1:8081",
        changeOrigin: true,
      },
      "/admin/ws": {
        target: "ws://127.0.0.1:8081",
        ws: true,
      },
    },
  },
});
