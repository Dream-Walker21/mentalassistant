# 心晴助手 Web 前端

正式前端骨架，基于 **Vue 3 + Vite + TypeScript + Pinia + Vue Router**。

## 启动

```bash
cd web/app
pnpm install
pnpm dev
```

打开 http://127.0.0.1:5173/ 。

开发时前端请求 `/data-api/*`，由 Vite 代理到 `http://127.0.0.1:8001`（data_service）。
需要改后端地址时设置环境变量：

```bash
VITE_API_BASE=http://127.0.0.1:8001
```

## 目录

```
src/
├── api/
│   ├── client.ts      # fetch 封装：Bearer token、401 自动 refresh 重试
│   └── types.ts       # 与 docs/API_CONTRACT.md 对应的类型
├── stores/auth.ts     # Pinia：access token 存内存，refresh 走 httpOnly cookie
├── router/index.ts    # 路由 + 登录守卫
├── views/
│   ├── LoginView.vue  # 登录 / 注册
│   └── ChatView.vue   # 聊天主界面
├── components/
│   ├── MessageBubble.vue
│   └── AvatarPanel.vue  # 立绘占位，后续接 Live2D
└── styles/main.css
```

## 已完成

- 登录 / 注册（含紧急联系人）
- access token 存内存 + refresh cookie 自动续期 + 401 重试
- 发送消息、渲染回复、加载态、错误态
- 对话历史加载、新建对话
- 风险等级徽标；高风险/危机时弹出风险评估窗口（气泡保持中性，避免给用户压力）
- 音频地址预留（`audio_url`，当前骨架会自动播放）

## 待办

- Live2D 立绘接入（`AvatarPanel.vue` 目前是占位）
- 逐字输出 / 打字机效果
- 对话列表侧栏
- 正式视觉设计与移动端适配打磨
- 真实后端联调（data_service 8001 + LangGraph 2024）

## 协作约定

- 接口变更先改 `docs/API_CONTRACT.md`，再同步 `src/api/types.ts`
- 前端只与 data_service 通信，不直接访问工作流或告警服务
- 改动前按 `AGENTS.md` §4 做影响评估
