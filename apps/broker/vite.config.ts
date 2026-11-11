import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "/broker/",
  plugins: [react()],
  // amazon-cognito-identity-js expects Node's `global`.
  define: { global: "globalThis" },
  server: { port: 5174, strictPort: true, proxy: { "/api": "http://localhost:8080" } },
  build: { sourcemap: false },
});
