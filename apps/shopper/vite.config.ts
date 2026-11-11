import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "/shopper/",
  plugins: [react()],
  server: { port: 5175, strictPort: true, proxy: { "/api": "http://localhost:8080" } },
  build: { sourcemap: false },
});
