import { DEFAULT_COMMAND, EXPRESSION_PARAMETERS, HIYORI_ACTIONS } from "./model-config.js";

const LOCAL_SDK_URL = "http://127.0.0.1:8084/";
const SDK_DEMO_URL = window.location.port === "8083" ? LOCAL_SDK_URL : "/live2d-sdk/";
const SDK_TARGET_ORIGIN = window.location.port === "8083" ? "http://127.0.0.1:8084" : window.location.origin;

export class Live2DAdapter {
  constructor(canvas, statusElement) {
    this.canvas = canvas;
    this.statusElement = statusElement;
    this.frame = null;
    this.thinkingTimer = null;
  }

  async mount() {
    this.setStatus("正在载入模型");
    this.frame = document.createElement("iframe");
    this.frame.id = "live2d-frame";
    this.frame.title = "Hiyori Free Live2D 模型";
    this.frame.src = SDK_DEMO_URL;
    Object.assign(this.frame.style, { position: "absolute", inset: "0", width: "100%", height: "100%", border: "0" });
    this.canvas.replaceWith(this.frame);
    await new Promise((resolve, reject) => {
      this.frame.addEventListener("load", resolve, { once: true });
      this.frame.addEventListener("error", () => reject(new Error("官方 SDK Demo 未启动，请先运行 8084 端口服务。")), { once: true });
    });
    this.setStatus("模型已就绪");
    await this.play(DEFAULT_COMMAND);
  }

  setStatus(text) { this.statusElement.textContent = text; }

  normalize(command) {
    const candidate = { ...DEFAULT_COMMAND, ...(command || {}) };
    if (!HIYORI_ACTIONS[candidate.action]) candidate.action = DEFAULT_COMMAND.action;
    if (!EXPRESSION_PARAMETERS[candidate.expression]) candidate.expression = DEFAULT_COMMAND.expression;
    candidate.intensity = Math.min(1, Math.max(0, Number(candidate.intensity) || DEFAULT_COMMAND.intensity));
    candidate.duration_ms = Math.min(10000, Math.max(500, Number(candidate.duration_ms) || DEFAULT_COMMAND.duration_ms));
    return candidate;
  }

  async play(command) {
    if (!this.frame?.contentWindow) return null;
    const normalized = this.normalize(command);
    this.frame.contentWindow.postMessage({ type: "xinqing-live2d-command", command: normalized }, SDK_TARGET_ORIGIN);
    this.setStatus(`${normalized.action} · ${normalized.expression}`);
    return normalized;
  }

  async startThinking() {
    this.stopThinking();
    const command = {
      action: "think",
      expression: "calm",
      gesture: "thinking",
      intensity: 0.35,
      duration_ms: 1200,
    };
    await this.play(command);
  }

  stopThinking() {
    if (this.thinkingTimer !== null) {
      window.clearInterval(this.thinkingTimer);
      this.thinkingTimer = null;
    }
  }

  async speak(audioUrl) {
    if (!audioUrl) return false;
    const audio = new Audio(audioUrl);
    audio.preload = "auto";
    try {
      await audio.play();
      return true;
    } catch {
      return false;
    }
  }

  destroy() { this.stopThinking(); this.frame?.remove(); this.frame = null; }
}
