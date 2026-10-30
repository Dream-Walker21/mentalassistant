import { computed, ref } from "vue";
import { defineStore } from "pinia";

import { apiRequest, setAccessToken, setRefreshHandler } from "@/api/client";
import type { AuthResponse, MeResponse, RefreshResponse, User } from "@/api/types";

export interface RegisterPayload {
  nickname: string;
  password: string;
  real_name?: string;
  emergency_contacts?: Array<{ name: string; phone: string; email: string }>;
}

export const useAuthStore = defineStore("auth", () => {
  const user = ref<User | null>(null);
  const token = ref("");
  const initialized = ref(false);

  let initPromise: Promise<void> | null = null;

  const isAuthenticated = computed(() => Boolean(token.value && user.value));

  function applySession(accessToken: string, nextUser: User): void {
    token.value = accessToken;
    user.value = nextUser;
    setAccessToken(accessToken);
  }

  function clearSession(): void {
    token.value = "";
    user.value = null;
    setAccessToken(null);
  }

  async function login(nickname: string, password: string): Promise<void> {
    const data = await apiRequest<AuthResponse>("/api/auth/login", {
      method: "POST",
      body: { nickname, password },
    });
    applySession(data.access_token, data.user);
  }

  async function register(payload: RegisterPayload): Promise<void> {
    const data = await apiRequest<AuthResponse>("/api/auth/register", {
      method: "POST",
      body: payload,
    });
    applySession(data.access_token, data.user);
  }

  async function logout(): Promise<void> {
    try {
      await apiRequest("/api/auth/logout", { method: "POST" });
    } catch {
      // 登出不依赖后端成功，本地状态必须清掉
    } finally {
      clearSession();
    }
  }

  /** 用 refresh cookie 换新 access token；失败返回 null，不抛错 */
  async function refresh(): Promise<string | null> {
    try {
      const data = await apiRequest<RefreshResponse>("/api/auth/refresh", { method: "POST" });
      token.value = data.access_token;
      setAccessToken(data.access_token);
      return data.access_token;
    } catch {
      return null;
    }
  }

  // 把 refresh 注入到 client，401 时自动调用
  setRefreshHandler(refresh);

  /** 应用启动时恢复会话：refresh → /me。只执行一次。 */
  async function initialize(): Promise<void> {
    if (initialized.value) return;
    if (!initPromise) {
      initPromise = (async () => {
        const nextToken = await refresh();
        if (nextToken) {
          try {
            const data = await apiRequest<MeResponse>("/api/auth/me");
            user.value = data.user;
          } catch {
            clearSession();
          }
        }
        initialized.value = true;
      })();
    }
    await initPromise;
  }

  return {
    user,
    token,
    initialized,
    isAuthenticated,
    login,
    register,
    logout,
    refresh,
    initialize,
  };
});
