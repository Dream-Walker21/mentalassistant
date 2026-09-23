import { Live2DAdapter } from "./live2d-adapter.js";

const STORAGE_KEY = "xinqing-live2d-session";
const state = { userId: "", displayName: "", conversationId: "", threadId: "", authToken: "", messages: [], busy: false, thinking: false };
let refreshPromise = null;
const $ = (id) => document.getElementById(id);
const els = {
  loginView: $("login-view"), chatView: $("chat-view"), loginForm: $("login-form"),
  nickname: $("nickname"), password: $("password"), realName: $("real-name"), contactName: $("contact-name"), contactPhone: $("contact-phone"), contactEmail: $("contact-email"),
  registerFields: $("register-fields"), loginTab: $("login-tab"), registerTab: $("register-tab"), authSubmit: $("auth-submit"), authSwitch: $("auth-switch"), loginError: $("login-error"),
  userLabel: $("user-label"), conversationTitle: $("conversation-title"), messageList: $("message-list"),
  composer: $("composer"), messageInput: $("message-input"), sendButton: $("send-button"),
  chatError: $("chat-error"), logout: $("logout-button"), newConversation: $("new-conversation"),
  avatarCanvas: $("avatar-canvas"), modelStatus: $("model-status"), avatarCommand: $("avatar-command"),
  portraitToggle: $("portrait-toggle"), historyButton: $("history-button"), avatarReply: $("avatar-reply"),
  assessmentModal: $("assessment-modal"), assessmentClose: $("assessment-close"), assessmentMetrics: $("assessment-metrics"), assessmentSummary: $("assessment-summary"),
};
let adapter;

function isMobilePlatform() {
  return window.matchMedia("(max-width: 780px)").matches || /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
}
function setPortraitMode(enabled, persist = true) {
  document.body.classList.toggle("portrait-mode", enabled);
  els.portraitToggle?.setAttribute("aria-pressed", String(enabled));
  if (els.portraitToggle) els.portraitToggle.textContent = enabled ? "▣" : "▯";
  if (persist) localStorage.setItem("xinqing-portrait-mode", enabled ? "1" : "0");
}
function showAvatarReply(content) {
  if (!els.avatarReply) return;
  els.avatarReply.textContent = content || "";
  els.avatarReply.classList.toggle("is-visible", Boolean(content));
}
function assessmentPercent(value, max) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.max(0, Math.min(100, Math.round(number / max * 100)));
}
function showAssessment(assessment) {
  if (!assessment || typeof assessment !== "object" || !els.assessmentModal) return;
  const metrics = [];
  const stress = assessmentPercent(assessment.stress_level, 5);
  if (stress !== null) metrics.push({ label: "压力等级", text: `${Number(assessment.stress_level).toFixed(0)}/5`, value: stress, color: stress >= 80 ? "#c2410c" : "#2563eb" });
  const confidence = assessmentPercent(assessment.confidence_level ?? assessment.confidence, (Number(assessment.confidence_level ?? assessment.confidence) <= 1 ? 1 : 100));
  if (confidence !== null) metrics.push({ label: "评估置信度", text: `${confidence}%`, value: confidence, color: "#7c3aed" });
  const level = String(assessment.risk_level || "").toLowerCase();
  if (level) {
    const levelData = { low: [25, "低风险", "#0f766e"], medium: [50, "中风险", "#ca8a04"], high: [75, "高风险", "#ea580c"], critical: [100, "危机风险", "#b42318"] }[level] || [0, level, "#64748b"];
    metrics.push({ label: "风险等级", text: levelData[1], value: levelData[0], color: levelData[2] });
  }
  if (!metrics.length) return;
  els.assessmentMetrics.replaceChildren();
  for (const item of metrics) {
    const card = document.createElement("div"); card.className = "assessment-metric";
    const ring = document.createElement("div"); ring.className = "assessment-ring"; ring.style.setProperty("--value", item.value); ring.style.setProperty("--ring-color", item.color);
    const value = document.createElement("strong"); value.textContent = item.text; ring.append(value);
    const label = document.createElement("span"); label.textContent = item.label; card.append(ring, label); els.assessmentMetrics.append(card);
  }
  const emotion = assessment.emotional_state ? `当前情绪：${assessment.emotional_state}。` : "";
  const issues = Array.isArray(assessment.main_issues) && assessment.main_issues.length ? `主要关注：${assessment.main_issues.join("、")}。` : "";
  els.assessmentSummary.textContent = `${emotion}${issues}` || "你可以把这份结果当作一次自我观察的参考。";
  els.assessmentModal.classList.remove("is-hidden");
}
function closeAssessment() { els.assessmentModal?.classList.add("is-hidden"); }

function saveSession() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    user_id: state.userId, display_name: state.displayName,
    conversation_id: state.conversationId, thread_id: state.threadId,
  }));
}
function loadSession() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null") || {}; } catch { return {}; }
}
async function doRefresh() {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    const response = await fetch("/data-api/api/auth/refresh", { method: "POST", credentials: "include" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || "refresh failed");
    state.authToken = body.access_token;
  })();
  try { await refreshPromise; } finally { refreshPromise = null; }
}
async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(state.authToken ? { Authorization: `Bearer ${state.authToken}` } : {}), ...(options.headers || {}) };
  let response = await fetch(path, { ...options, headers, credentials: "include" });
  if (response.status === 401 && state.authToken && !path.includes("/api/auth/")) {
    await doRefresh();
    headers.Authorization = `Bearer ${state.authToken}`;
    response = await fetch(path, { ...options, headers, credentials: "include" });
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || body.message || `请求失败（${response.status}）`);
  return body;
}
async function ensureUser() {
  return request(`/data-api/api/users/${encodeURIComponent(state.userId)}`);
}
async function ensureConversation() {
  const result = await request(`/data-api/api/users/${encodeURIComponent(state.userId)}/conversations`, {
    method: "POST", body: JSON.stringify({ conversation_id: state.conversationId || undefined, title: "新的对话" }),
  });
  state.conversationId = result.conversation_id;
  saveSession();
}
async function loadMessages() {
  const result = await request(`/data-api/api/conversations/${encodeURIComponent(state.conversationId)}/messages?limit=100`);
  state.messages = result.messages || [];
  renderMessages();
}
function renderMessages() {
  els.messageList.replaceChildren();
  if (!state.messages.length) {
    const empty = document.createElement("div"); empty.className = "empty-state"; empty.textContent = "从一句“你好”开始吧。"; els.messageList.append(empty); return;
  }
  for (const item of state.messages) {
    const role = item.role === "user" ? "user" : "assistant";
    const row = document.createElement("div"); row.className = `message ${role}`;
    const bubble = document.createElement("div"); bubble.className = "bubble"; bubble.textContent = item.content || "";
    row.append(bubble); els.messageList.append(row);
  }
  const latestAssistant = [...state.messages].reverse().find((item) => item.role !== "user");
  if (!state.thinking && document.body.classList.contains("portrait-mode")) showAvatarReply(latestAssistant?.content || "");
  if (state.thinking) {
    const row = document.createElement("div"); row.className = "message assistant thinking-message";
    const bubble = document.createElement("div"); bubble.className = "bubble"; bubble.textContent = "正在思考…";
    row.append(bubble); els.messageList.append(row);
  }
  els.messageList.scrollTop = els.messageList.scrollHeight;
}
async function chatRequest(query) {
  const result = await request("/data-api/chat", {
    method: "POST",
    body: JSON.stringify({

      input_type: "text",
      content: query,
      conversation_id: state.conversationId,
      ...(state.threadId ? { thread_id: state.threadId } : {}),
    }),
  });
  if (result.thread_id && result.thread_id !== state.threadId) {
    state.threadId = result.thread_id; saveSession();
  }
  return result;
}
function addMessage(role, content) {
  state.messages.push({ role, content }); renderMessages();
}
async function sendMessage(event) {
  event.preventDefault();
  const query = els.messageInput.value.trim();
  if (!query || state.busy) return;
  state.busy = true; state.thinking = true; els.sendButton.disabled = true; els.chatError.textContent = ""; addMessage("user", query); els.messageInput.value = "";
  renderMessages();
  await adapter?.startThinking();
  els.avatarCommand.textContent = "think · calm";
  try {
    const result = await chatRequest(query);
    const data = result.data || {};
    const response = data.text || "我在这里，刚才没有生成可显示的回复。";
    showAssessment({ risk_level: data.risk_level });
    adapter?.stopThinking();
    state.thinking = false; renderMessages();
    addMessage("assistant", response);
    showAvatarReply(response);
    if (data.avatar_command) {
      await adapter?.play(data.avatar_command); els.avatarCommand.textContent = `${data.avatar_command.action || "idle"} · ${data.avatar_command.expression || "calm"}`;
    }
    if (data.audio_url) await adapter?.speak("/data-api" + data.audio_url);
  } catch (error) {
    adapter?.stopThinking();
    state.thinking = false; renderMessages();
    showAvatarReply("");
    els.chatError.textContent = error.message; addMessage("assistant", "这次连接没有完成，我们稍后再试一次。");
  } finally {
    adapter?.stopThinking();
    state.thinking = false; state.busy = false; els.sendButton.disabled = false; els.messageInput.focus();
  }
}
let registerMode = false;
function setAuthMode(register) {
  registerMode = register; els.registerFields.classList.toggle("is-hidden", !register); els.registerFields.querySelectorAll("input").forEach((input) => { input.disabled = !register; }); els.loginTab.classList.toggle("active", !register); els.registerTab.classList.toggle("active", register); els.authSubmit.firstChild.textContent = register ? "创建账户并进入 " : "登录并进入 "; els.password.autocomplete = register ? "new-password" : "current-password";
  els.authSwitch.textContent = register ? "已有账户？返回登录" : '还没有账户？点击"注册"';
}
async function enterApp(event) {
  event?.preventDefault(); els.loginError.textContent = "";
  try {
    const nickname = els.nickname.value.trim(); const password = els.password.value;
    const endpoint = registerMode ? "/data-api/api/auth/register" : "/data-api/api/auth/login";
    const body = registerMode ? { nickname, password, real_name: els.realName.value.trim(), emergency_contacts: [{ name: els.contactName.value.trim(), phone: els.contactPhone.value.trim(), email: els.contactEmail.value.trim() }] } : { nickname, password };
    const authResult = await request(endpoint, { method: "POST", body: JSON.stringify(body) });
    state.authToken = authResult.access_token; state.userId = authResult.user.user_id; state.displayName = authResult.user.display_name || nickname;
    const previous = loadSession();
    if (previous.user_id === state.userId) { state.conversationId = previous.conversation_id || ""; state.threadId = ""; }
    await ensureUser(); await ensureConversation(); await loadMessages();
    els.userLabel.textContent = state.displayName; els.conversationTitle.textContent = state.conversationId;
    els.loginView.classList.add("is-hidden"); els.chatView.classList.remove("is-hidden");
    adapter = new Live2DAdapter(els.avatarCanvas, els.modelStatus);
    await adapter.mount();
  } catch (error) { els.loginError.textContent = error.message; }
}
function logout() {
  request("/data-api/api/auth/logout", { method: "POST" }).catch(() => {}); adapter?.destroy(); localStorage.removeItem(STORAGE_KEY);
  state.userId = ""; state.displayName = ""; state.authToken = ""; state.conversationId = ""; state.threadId = ""; state.messages = [];
  els.chatView.classList.add("is-hidden"); els.loginView.classList.remove("is-hidden"); els.nickname.focus();
}
async function newConversation() {
  state.conversationId = ""; state.threadId = ""; state.messages = []; await ensureConversation();
  els.conversationTitle.textContent = state.conversationId; renderMessages(); saveSession();
}
els.loginForm.addEventListener("submit", enterApp);
els.loginTab.addEventListener("click", () => setAuthMode(false)); els.registerTab.addEventListener("click", () => setAuthMode(true));
els.authSwitch.addEventListener("click", () => setAuthMode(!registerMode));
els.composer.addEventListener("submit", sendMessage);
els.logout.addEventListener("click", logout);
els.newConversation.addEventListener("click", () => newConversation().catch((error) => { els.chatError.textContent = error.message; }));
els.portraitToggle.addEventListener("click", () => setPortraitMode(!document.body.classList.contains("portrait-mode")));
els.historyButton.addEventListener("click", () => document.body.classList.toggle("history-open"));
els.assessmentClose.addEventListener("click", closeAssessment);
els.assessmentModal.addEventListener("click", (event) => { if (event.target === els.assessmentModal) closeAssessment(); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeAssessment(); });
els.messageInput.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); els.composer.requestSubmit(); } });
async function restoreSession() {
  try {
    await doRefresh();
    const me = await request("/data-api/api/auth/me");
    state.userId = me.user.user_id;
    state.displayName = me.user.display_name || me.user.nickname || "";
    const previous = loadSession();
    if (previous.user_id === state.userId) { state.conversationId = previous.conversation_id || ""; state.threadId = previous.thread_id || ""; }
    await ensureUser(); await ensureConversation(); await loadMessages();
    els.userLabel.textContent = state.displayName; els.conversationTitle.textContent = state.conversationId;
    els.loginView.classList.add("is-hidden"); els.chatView.classList.remove("is-hidden");
    adapter = new Live2DAdapter(els.avatarCanvas, els.modelStatus);
    await adapter.mount();
  } catch { /* refresh failed, stay on login view */ }
}
setAuthMode(false);
restoreSession();
const savedPortrait = localStorage.getItem("xinqing-portrait-mode");
setPortraitMode(savedPortrait === "1" || (savedPortrait === null && isMobilePlatform()), false);
window.matchMedia("(max-width: 780px)").addEventListener?.("change", (event) => {
  if (localStorage.getItem("xinqing-portrait-mode") === null) setPortraitMode(event.matches, false);
});
