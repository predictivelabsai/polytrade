import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          markdown: ["react-markdown", "remark-gfm"],
          wallet: ["viem"],
        },
      },
    },
  },
  server: { port: 5173 },
  preview: { port: 5173 },
});
