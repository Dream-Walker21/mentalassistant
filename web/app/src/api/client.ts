/**
 * HTTP 请求封装。
 *
 * 关键行为：
 * 1. access token 只存在内存（模块变量），不写 localStorage —— 见 docs/AUTH_DESIGN.md §7
 * 2. 所有请求带 credentials: "include"，refresh token 由浏览器自动放进 httpOnly cookie
 * 3. 业务接口返回 401 时，自动 refresh 一次并重试原请求，用户无感
 */

const API_BASE = import.meta.env.VITE_API_BASE ?? "/data-api";

let accessToken: string | null = null;
let refreshHandler: (() => Promise<string | null>) | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

/** 由 auth store 注入，避免 client 和 store 循环依赖 */
export function setRefreshHandler(handler: (() => Promise<string | null>) | null): void {
  refreshHandler = handler;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, headers, ...rest } = options;

  const doFetch = (token: string | null): Promise<Response> => {
    const finalHeaders = new Headers(headers);
    if (body !== undefined) finalHeaders.set("Content-Type", "application/json");
    if (token) finalHeaders.set("Authorization", `Bearer ${token}`);

    return fetch(`${API_BASE}${path}`, {
      ...rest,
      credentials: "include",
      headers: finalHeaders,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  };

  let response = await doFetch(accessToken);

  // /api/auth/* 自身失败不触发刷新，避免无限递归
  if (response.status === 401 && !path.startsWith("/api/auth/") && refreshHandler) {
    const nextToken = await refreshHandler();
    if (nextToken) response = await doFetch(nextToken);
  }

  const payload: unknown = await response.json().catch(() => ({}));

  if (!response.ok) {
    const data = payload as { error?: string; error_code?: string };
    throw new ApiError(data.error ?? `请求失败（${response.status}）`, response.status, data.error_code);
  }

  return payload as T;
}

/** 后端音频地址需要经过同一代理前缀 */
export function toAudioUrl(path: string): string {
  return `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}
