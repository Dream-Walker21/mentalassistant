<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { ApiError, apiRequest, toAudioUrl } from "@/api/client";
import type {
  AvatarCommand,
  ChatResponse,
  ConversationResponse,
  Message,
  MessagesResponse,
  RiskLevel,
} from "@/api/types";
import AvatarPanel from "@/components/AvatarPanel.vue";
import MessageBubble from "@/components/MessageBubble.vue";
import RiskModal from "@/components/RiskModal.vue";
import { useAuthStore } from "@/stores/auth";

const STORAGE_KEY = "xinqing-web-session";

const auth = useAuthStore();
const router = useRouter();

const messages = ref<Message[]>([]);
const input = ref("");
const sending = ref(false);
const loading = ref(true);
const error = ref("");
const conversationId = ref("");
const threadId = ref("");
const riskLevel = ref<RiskLevel>("low");
const riskModalOpen = ref(false);
const avatarCommand = ref<AvatarCommand | null>(null);
const listEl = ref<HTMLElement | null>(null);
const inputEl = ref<HTMLTextAreaElement | null>(null);

const riskLabel = computed(() => {
  const labels: Record<RiskLevel, string> = {
    low: "低风险",
    medium: "中风险",
    high: "高风险",
    critical: "危机",
  };
  return labels[riskLevel.value];
});

const isCrisis = computed(() => riskLevel.value === "high" || riskLevel.value === "critical");

interface SavedSession {
  user_id?: string;
  conversation_id?: string;
  thread_id?: string;
}

function readSession(): SavedSession {
  try {
    return (JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null") ?? {}) as SavedSession;
  } catch {
    return {};
  }
}

function saveSession(): void {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      user_id: auth.user?.user_id ?? "",
      conversation_id: conversationId.value,
      thread_id: threadId.value,
    }),
  );
}

async function scrollToBottom(): Promise<void> {
  await nextTick();
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight;
}

async function ensureConversation(): Promise<void> {
  const userId = auth.user?.user_id;
  if (!userId) return;

  const previous = readSession();
  const saved = previous.user_id === userId ? previous.conversation_id : undefined;

  const data = await apiRequest<ConversationResponse>(
    `/api/users/${encodeURIComponent(userId)}/conversations`,
    {
      method: "POST",
      body: { conversation_id: saved || undefined, title: "新的对话" },
    },
  );
  conversationId.value = data.conversation_id;
  saveSession();
}

async function loadMessages(): Promise<void> {
  if (!conversationId.value) return;

  const data = await apiRequest<MessagesResponse>(
    `/api/conversations/${encodeURIComponent(conversationId.value)}/messages?limit=100`,
  );
  messages.value = data.messages ?? [];
  await scrollToBottom();
}

async function send(): Promise<void> {
  const content = input.value.trim();
  if (!content || sending.value) return;

  error.value = "";
  messages.value.push({ role: "user", content });
  input.value = "";
  sending.value = true;
  // 点击发送按钮后焦点会落在按钮上，立即还回输入框
  inputEl.value?.focus();
  avatarCommand.value = {
    version: 1,
    action: "think",
    expression: "calm",
    gesture: "none",
    intensity: 0.3,
    duration_ms: 1200,
  };
  void scrollToBottom();

  try {
    if (!conversationId.value) await ensureConversation();

    const result = await apiRequest<ChatResponse>("/chat", {
      method: "POST",
      body: {
        input_type: "text",
        content,
        conversation_id: conversationId.value,
        thread_id: threadId.value || undefined,
      },
    });

    const data = result.data;
    messages.value.push({ role: "assistant", content: data.text });
    riskLevel.value = data.risk_level;
    // 高风险只在右上角变色 + 弹一次风险评估，不给气泡加红色压力
    if (data.risk_level === "high" || data.risk_level === "critical") {
      riskModalOpen.value = true;
    }
    avatarCommand.value = data.avatar_command;
    threadId.value = result.thread_id || threadId.value;
    saveSession();

    if (data.audio_url) {
      const audio = new Audio(toAudioUrl(data.audio_url));
      void audio.play().catch(() => undefined);
    }
  } catch (err) {
    error.value = err instanceof ApiError || err instanceof Error ? err.message : "连接失败，请稍后重试";
    messages.value.push({ role: "assistant", content: "这次连接没有完成，我们稍后再试一次。" });
  } finally {
    sending.value = false;
    await scrollToBottom();
    // 回复结束后继续保持输入框聚焦，可以直接打下一条
    inputEl.value?.focus();
  }
}

async function startNewConversation(): Promise<void> {
  error.value = "";
  conversationId.value = "";
  threadId.value = "";
  messages.value = [];
  riskLevel.value = "low";
  riskModalOpen.value = false;
  avatarCommand.value = null;

  try {
    await ensureConversation();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "创建会话失败";
  }
}

async function logout(): Promise<void> {
  await auth.logout();
  await router.replace("/login");
}

onMounted(async () => {
  try {
    await ensureConversation();
    await loadMessages();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "载入对话失败";
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div class="chat-page">
    <div class="chat-shell">
      <aside class="avatar-column">
        <div class="user-row">
          <div class="user-avatar">{{ (auth.user?.display_name || "心").slice(0, 1) }}</div>
          <div class="user-meta">
            <p class="user-name">{{ auth.user?.display_name }}</p>
            <p class="user-id">{{ auth.user?.user_id }}</p>
          </div>
        </div>

        <AvatarPanel :command="avatarCommand" :thinking="sending" />

        <div class="side-actions">
          <button type="button" class="ghost" @click="startNewConversation">新对话</button>
          <button type="button" class="ghost" @click="logout">退出登录</button>
        </div>
      </aside>

      <main class="chat-main">
        <header class="chat-header">
          <div>
            <h2>和心晴聊聊</h2>
            <p class="chat-subtitle">这里没有评判，只有倾听。</p>
          </div>
          <button
            v-if="isCrisis"
            type="button"
            class="risk-badge is-clickable"
            :class="riskLevel"
            title="查看风险评估"
            @click="riskModalOpen = true"
          >
            {{ riskLabel }}
          </button>
          <span v-else class="risk-badge" :class="riskLevel">{{ riskLabel }}</span>
        </header>

        <div ref="listEl" class="message-list">
          <div v-if="loading" class="empty-state">正在载入对话…</div>
          <div v-else-if="!messages.length" class="empty-state">
            <strong>从一句「你好」开始吧。</strong>
            <span>你可以聊聊今天的心情、学习压力，或者任何想说的话。</span>
          </div>

          <MessageBubble
            v-for="(message, index) in messages"
            :key="`${index}-${message.role}`"
            :role="message.role"
            :content="message.content"
          />

          <div v-if="sending" class="message assistant">
            <div class="bubble thinking">正在思考</div>
          </div>
        </div>

        <p v-if="error" class="error-banner" role="alert">{{ error }}</p>

        <form class="composer" @submit.prevent="send">
          <textarea
            ref="inputEl"
            v-model="input"
            rows="1"
            placeholder="说点什么吧…（Enter 发送，Shift + Enter 换行）"
            @keydown.enter.exact.prevent="send"
          />
          <button type="submit" class="send-button" :disabled="sending || !input.trim()">
            {{ sending ? "思考中…" : "发送" }}
          </button>
        </form>
      </main>
    </div>

    <RiskModal :open="riskModalOpen" :level="riskLevel" @close="riskModalOpen = false" />
  </div>
</template>

<style scoped>
.chat-page {
  height: 100vh;
  padding: 24px;
}

.chat-shell {
  max-width: 1280px;
  height: 100%;
  margin: 0 auto;
  display: grid;
  /* 立绘区约占 40%，比旧版（约 45%）窄一点 */
  grid-template-columns: minmax(360px, 2fr) minmax(0, 3fr);
  gap: 20px;
}

.avatar-column {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 22px;
  border: 1px solid rgba(255, 255, 255, 0.8);
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.86);
  box-shadow: 0 18px 44px rgba(31, 41, 51, 0.08);
}

.user-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.user-avatar {
  width: 44px;
  height: 44px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  border-radius: 14px;
  color: #fff;
  font-weight: 700;
  background: linear-gradient(135deg, var(--primary), var(--accent));
}

.user-meta {
  min-width: 0;
}

.user-name {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
}

.user-id {
  margin: 2px 0 0;
  color: var(--muted);
  font-size: 11px;
  word-break: break-all;
}

.side-actions {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chat-main {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.8);
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 18px 44px rgba(31, 41, 51, 0.08);
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 24px 16px;
  border-bottom: 1px solid #eef2f1;
}

.chat-header h2 {
  margin: 0;
  font-size: 18px;
}

.chat-subtitle {
  margin: 3px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.risk-badge {
  flex-shrink: 0;
  padding: 6px 12px;
  border: 0;
  border-radius: 999px;
  font-family: inherit;
  font-size: 12px;
  font-weight: 700;
}

.risk-badge.is-clickable {
  cursor: pointer;
}

.risk-badge.is-clickable:hover {
  filter: brightness(0.96);
}

.risk-badge.low {
  background: #e8f5f1;
  color: #0f766e;
}

.risk-badge.medium {
  background: #fef3c7;
  color: #92400e;
}

.risk-badge.high {
  background: #ffedd5;
  color: #c2410c;
}

.risk-badge.critical {
  background: #fee4e2;
  color: #b42318;
}

.message-list {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
  padding: 24px;
}

.composer {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  padding: 16px 20px 20px;
  border-top: 1px solid #eef2f1;
}

.composer textarea {
  flex: 1;
  min-height: 48px;
  max-height: 132px;
  padding: 13px 16px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: #fbfcfc;
  outline: none;
  line-height: 1.5;
  resize: none;
}

.composer textarea:focus {
  border-color: var(--primary);
  background: #fff;
  box-shadow: 0 0 0 3px rgba(47, 125, 118, 0.14);
}

.send-button {
  flex-shrink: 0;
  min-width: 92px;
  height: 48px;
  border: 0;
  border-radius: 16px;
  color: #fff;
  font-weight: 700;
  background: linear-gradient(135deg, var(--primary), var(--primary-strong));
  box-shadow: 0 8px 18px rgba(47, 125, 118, 0.22);
}

@media (max-width: 900px) {
  .chat-page {
    height: auto;
    min-height: 100vh;
    padding: 12px;
  }

  .chat-shell {
    grid-template-columns: 1fr;
    height: auto;
  }

  .avatar-column {
    flex-direction: column;
    align-items: stretch;
  }

  .side-actions {
    margin-top: 0;
    flex-direction: row;
  }

  .chat-main {
    min-height: 68vh;
  }
}
</style>
