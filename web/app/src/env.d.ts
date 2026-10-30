/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 后端接口前缀，默认 /data-api（由 Vite 代理到 data_service:8001） */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare module "*.vue" {
  import type { DefineComponent } from "vue";

  const component: DefineComponent<Record<string, never>, Record<string, never>, unknown>;
  export default component;
}
