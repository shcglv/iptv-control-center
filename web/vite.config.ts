import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build output is shipped inside the Python package so the API can serve it
// without a second container or a CDN.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/iptvcc/static",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/p": "http://127.0.0.1:8000",
      "/healthz": "http://127.0.0.1:8000",
    },
  },
});
