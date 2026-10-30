<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { ApiError } from "@/api/client";
import { useAuthStore } from "@/stores/auth";

type Mode = "login" | "register";

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();

const mode = ref<Mode>("login");
const nickname = ref("");
const password = ref("");
const realName = ref("");
const contactName = ref("");
const contactPhone = ref("");
const contactEmail = ref("");
const error = ref("");
const submitting = ref(false);

function switchMode(next: Mode): void {
  mode.value = next;
  error.value = "";
}

async function submit(): Promise<void> {
  if (submitting.value) return;
  error.value = "";

  const name = nickname.value.trim();
  if (!name || !password.value) {
    error.value = "请填写昵称和密码";
    return;
  }

  submitting.value = true;
  try {
    if (mode.value === "login") {
      await auth.login(name, password.value);
    } else {
      const hasContact = Boolean(
        contactName.value.trim() || contactPhone.value.trim() || contactEmail.value.trim(),
      );
      await auth.register({
        nickname: name,
        password: password.value,
        real_name: realName.value.trim() || undefined,
        emergency_contacts: hasContact
          ? [
              {
                name: contactName.value.trim(),
                phone: contactPhone.value.trim(),
                email: contactEmail.value.trim(),
              },
            ]
          : undefined,
      });
    }

    const redirect = typeof route.query.redirect === "string" ? route.query.redirect : "/chat";
    await router.replace(redirect);
  } catch (err) {
    error.value = err instanceof ApiError || err instanceof Error ? err.message : "请求失败，请稍后重试";
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="brand">
        <div class="brand-mark">心</div>
        <h1>心晴助手</h1>
        <p>一个愿意听你说话的数字伙伴</p>
      </div>

      <div class="tabs" role="tablist">
        <button
          type="button"
          role="tab"
          :aria-selected="mode === 'login'"
          :class="{ active: mode === 'login' }"
          @click="switchMode('login')"
        >
          登录
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="mode === 'register'"
          :class="{ active: mode === 'register' }"
          @click="switchMode('register')"
        >
          注册
        </button>
      </div>

      <form class="auth-form" @submit.prevent="submit">
        <label class="field">
          <span>昵称</span>
          <input
            v-model.trim="nickname"
            type="text"
            autocomplete="username"
            placeholder="给自己起个名字"
            required
          />
        </label>

        <label class="field">
          <span>密码</span>
          <input
            v-model="password"
            type="password"
            :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
            placeholder="输入密码"
            required
          />
        </label>

        <template v-if="mode === 'register'">
          <label class="field">
            <span>真实姓名 <em>选填</em></span>
            <input v-model.trim="realName" type="text" autocomplete="name" placeholder="用于紧急联系" />
          </label>

          <fieldset class="contacts">
            <legend>紧急联系人 <em>选填</em></legend>
            <p class="contacts-hint">仅在危机情况下用于联系可信任的人。</p>
            <label class="field">
              <span>姓名</span>
              <input v-model.trim="contactName" type="text" autocomplete="name" placeholder="联系人姓名" />
            </label>
            <div class="field-row">
              <label class="field">
                <span>电话</span>
                <input v-model.trim="contactPhone" type="tel" autocomplete="tel" placeholder="手机号" />
              </label>
              <label class="field">
                <span>邮箱</span>
                <input
                  v-model.trim="contactEmail"
                  type="email"
                  autocomplete="email"
                  placeholder="name@example.com"
                />
              </label>
            </div>
          </fieldset>
        </template>

        <p v-if="error" class="form-error" role="alert">{{ error }}</p>

        <button class="primary" type="submit" :disabled="submitting">
          {{ submitting ? "请稍候…" : mode === "login" ? "登录并进入" : "创建账户并进入" }}
        </button>
      </form>

      <button
        type="button"
        class="switch-hint"
        @click="switchMode(mode === 'login' ? 'register' : 'login')"
      >
        {{ mode === "login" ? "还没有账户？点击注册" : "已有账户？返回登录" }}
      </button>

      <p class="privacy-note">对话内容仅用于提供支持，不构成医学诊断。</p>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
}

.login-card {
  width: min(460px, 100%);
  padding: 32px;
  border: 1px solid rgba(255, 255, 255, 0.7);
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(14px);
  box-shadow: 0 24px 60px rgba(31, 41, 51, 0.12);
}

.brand {
  margin-bottom: 24px;
  text-align: center;
}

.brand-mark {
  width: 56px;
  height: 56px;
  margin: 0 auto 12px;
  display: grid;
  place-items: center;
  border-radius: 18px;
  color: #fff;
  font-size: 26px;
  font-weight: 700;
  background: linear-gradient(135deg, var(--primary), var(--accent));
  box-shadow: 0 10px 24px rgba(47, 125, 118, 0.3);
}

.brand h1 {
  margin: 0;
  font-size: 24px;
  letter-spacing: 0.04em;
}

.brand p {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 13px;
}

.tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px;
  margin-bottom: 20px;
  padding: 4px;
  border-radius: 14px;
  background: #eef2f1;
}

.tabs button {
  padding: 10px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--muted);
  font-weight: 600;
}

.tabs button.active {
  background: #fff;
  color: var(--primary-strong);
  box-shadow: 0 2px 8px rgba(31, 41, 51, 0.08);
}

.auth-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--ink);
}

.field > span {
  font-weight: 600;
}

.field em {
  margin-left: 4px;
  color: var(--muted);
  font-style: normal;
  font-weight: 400;
}

.field input {
  width: 100%;
  padding: 11px 13px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: #fbfcfc;
  outline: none;
  transition:
    border-color 0.15s ease,
    box-shadow 0.15s ease;
}

.field input:focus {
  border-color: var(--primary);
  background: #fff;
  box-shadow: 0 0 0 3px rgba(47, 125, 118, 0.14);
}

.contacts {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 14px;
  border: 1px dashed var(--line);
  border-radius: 14px;
}

.contacts legend {
  padding: 0 6px;
  font-size: 13px;
  font-weight: 600;
}

.contacts-hint {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
}

.field-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.form-error {
  margin: 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: #fff1f0;
  color: #b42318;
  font-size: 13px;
}

.primary {
  margin-top: 4px;
  padding: 13px;
  border: 0;
  border-radius: 14px;
  color: #fff;
  font-size: 15px;
  font-weight: 700;
  background: linear-gradient(135deg, var(--primary), var(--primary-strong));
  box-shadow: 0 10px 22px rgba(47, 125, 118, 0.25);
  transition:
    transform 0.12s ease,
    box-shadow 0.12s ease;
}

.primary:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 14px 26px rgba(47, 125, 118, 0.3);
}

.switch-hint {
  width: 100%;
  margin-top: 16px;
  border: 0;
  background: none;
  color: var(--primary-strong);
  font-size: 13px;
}

.privacy-note {
  margin: 18px 0 0;
  color: #94a3b8;
  font-size: 11px;
  line-height: 1.6;
  text-align: center;
}

@media (max-width: 480px) {
  .login-card {
    padding: 24px 20px;
    border-radius: 22px;
  }

  .field-row {
    grid-template-columns: 1fr;
  }
}
</style>
