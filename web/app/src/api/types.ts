/**
 * 与 docs/API_CONTRACT.md 保持一致的前端类型定义。
 * 改接口时先改契约文档，再同步这里。
 */

export interface User {
  user_id: string;
  display_name: string;
  nickname?: string;
}

export interface AuthResponse {
  status: string;
  access_token: string;
  user: User;
}

export interface MeResponse {
  status: string;
  user: User;
}

export interface RefreshResponse {
  status: string;
  access_token: string;
}

export type Intent = "daily_support" | "condition_judgement" | "crisis";

export type RiskLevel = "low" | "medium" | "high" | "critical";

/** 立绘动作指令，白名单由工作流保证 */
export interface AvatarCommand {
  version: number;
  action: string;
  expression: string;
  gesture: string;
  intensity: number;
  duration_ms: number;
}

export interface RiskAssessment {
  emotional_state: string;
  stress_level: number;
  main_issues: string[];
  urgency: string;
  support_needs: string[];
  risk_flag: boolean;
  risk_level: RiskLevel;
  risk_signals: string[];
  confidence_level: number;
}

export interface ChatData {
  text: string;
  audio_url?: string | null;
  avatar_command: AvatarCommand;
  intent: Intent;
  risk_level: RiskLevel;
}

export interface ChatResponse {
  status: string;
  data: ChatData;
  thread_id: string;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
}

export interface MessagesResponse {
  status: string;
  messages: Message[];
}

export interface ConversationResponse {
  status: string;
  conversation_id: string;
  title?: string;
}
