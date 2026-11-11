import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Local dev: this server is the front door, like CloudFront in AWS. /api goes to the API (uvicorn on 8080),
// /broker/ and /shopper/ to their own dev servers (npm run dev:broker, npm run dev:shopper).
export default defineConfig({
  base: "/",
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://localhost:8080",
      "/broker": { target: "http://localhost:5174", ws: true },
      "/shopper": { target: "http://localhost:5175", ws: true },
    },
  },
  build: { sourcemap: false },
});
