import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The browser calls /api/...; Vite forwards it to the local P1 API (default 127.0.0.1:8000).
const target = process.env.P1_API_URL ?? "http://127.0.0.1:8000";
const proxy = { "/api": { target, rewrite: (p: string) => p.replace(/^\/api/, "") } };

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
});
