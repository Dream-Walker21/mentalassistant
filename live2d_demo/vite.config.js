import { defineConfig } from "vite";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));

export default defineConfig({
  root: projectRoot,
  server: {
    fs: { strict: true },
    proxy: {
      "/data-api": {
        target: "http://127.0.0.1:8001",
        rewrite: (path) => path.replace(/^\/data-api/, ""),
      },
      "/langgraph-api": {
        target: "http://127.0.0.1:2024",
        rewrite: (path) => path.replace(/^\/langgraph-api/, ""),
      },
    },
  },
});
