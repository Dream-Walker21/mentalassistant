import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      // 前端只跟 data_service（8001）说话，见 docs/API_CONTRACT.md
      "/data-api": {
        target: "http://127.0.0.1:8001",
        rewrite: (path) => path.replace(/^\/data-api/, ""),
      },
    },
  },
});
