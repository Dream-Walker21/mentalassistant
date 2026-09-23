# 心晴助手 鉴权机制设计文档

> 状态：JWT 鉴权已落地（data_service + 前端测试面板），langgraph 服务间鉴权待蒋状钊接入。
> 方案：C（data_service 兼中间层统一鉴权）+ 企业级 JWT。
> 关联：`API_CONTRACT.md §7`、`AGENTS.md §3 接口边界`。

---

## 1. 现状与问题

### 1.1 三个服务三套鉴权，互不打通

| 服务 | 端口 | 鉴权方式 | 实际状态 |
|---|---|---|---|
| `data_service.py` | 8001 | `require_api_token` 校验 `DATA_API_TOKEN`（静态 key） | `.env` 里空，全放行；用户 bearer token 带了但不验 |
| `alert.py` 管理后台 | 5000 | Flask session + `admin_users` 表 + 角色权限矩阵 | ✅ 最完整，但与用户系统隔离 |
| `app.py`/langgraph | 2024 | 无 | 任何人可调 |

### 1.2 四个宏观问题

1. **契约与实现脱节**：`API_CONTRACT.md §7.2` 写"用户匿名"，实际已做实名注册系统（nickname/password/real_name/emergency_contacts）。
2. **身份传递链路断裂**：中间层未建，前端直连 data_service 和 langgraph，token 发出去无人验。
3. **鉴权边界未划分**：用户级、服务间、公开三种鉴权需求混在一个 `require_api_token` 里。
4. **token 存储不可扩展**：`session_user` 全表扫描 + 逐行 `check_password_hash`（bcrypt 级比对），无法索引查找。

---

## 2. 设计决策

- **方案 C**：等中间层建好后，在中间层做统一鉴权入口，下游服务用服务间鉴权信任中间层。
- **JWT**：企业级标准，双 token 模型（access + refresh）。
- **不引入**：OAuth2/OIDC、PyJWT 以外的鉴权框架、RBAC 给用户端（用户只有"自己"vs"别人"两种权限）。
- **不动**：alert.py 管理后台鉴权（Flask session + 角色权限已完整，与用户 JWT 本就是两套）。

---

## 3. 架构设计

```
┌─ 前端 ───────────────────────────────────────────────────────────┐
│  登录 → 拿 access_token(内存) + refresh_token(httpOnly cookie)    │
│  请求 → Authorization: Bearer <access_token>                     │
└─────────┬─────────────────────────────────────────────────────────┘
          │  唯一入口
          ▼
┌─ data_service (8001) ─────────────────────────────────────────────┐
│  /api/auth/login    验密码 → 签 JWT                                    │
│  /api/auth/refresh  验 refresh → 签新 access                           │
│  /api/auth/me       验 access → 返回用户信息                           │
│  /chat 等业务    验 access → 提取 user_id → 调下游(带服务间key)     │
│  /api/* 数据     验 access → 数据 CRUD                             │
└────┬───────────────────────────────────────────────────────────────┘
     │ 服务间静态 key
     ▼
┌─ langgraph (2024) ─┐   ┌─ alert (5000) ─┐
│  只信任 data_service  │   │  只信任工作流调用 │
│  不验JWT, 验服务间key │   │  管理后台保持独立 │
└─────────────────────┘   └─────────────────┘
```

**核心原则**：JWT 只在 data_service 验一次，langgraph/alert 用服务间鉴权（静态 key）信任 data_service。认证在网关层收敛，下游做授权信任。

---

## 4. Token 模型

### 4.1 双 token

| Token | 有效期 | 存储位置 | 用途 |
|---|---|---|---|
| Access Token | 15-30 分钟 | 前端内存（不存 localStorage，防 XSS） | 每次 API 调用带在 `Authorization: Bearer` header |
| Refresh Token | 7-30 天 | httpOnly + Secure cookie；DB 存 SHA-256 哈希 | access 过期后用它换新 access |

**设计理由**：access 短命→泄露窗口小；refresh 长命但只走 `/api/auth/refresh` 一个接口，可服务端吊销。

### 4.2 JWT payload

签名算法：HS256，密钥 `JWT_SECRET` 环境变量。

```json
{
  "sub": "user-xxx",
  "exp": 1735689600,
  "iat": 1735686000,
  "jti": "uuid"
}
```

> HS256 vs RS256：企业级用 RS256（公私钥分离，下游可验签），但本项目中间层是唯一验签点，HS256 够了。若以后下游也要独立验 JWT，再换 RS256。

### 4.3 Refresh token 存储

复用 `auth_sessions` 表，字段调整：

| 字段 | 类型 | 说明 |
|---|---|---|
| `token_hash` | TEXT | `SHA-256(refresh_token)`，可索引查找（不再用 bcrypt） |
| `user_id` | TEXT | 关联 users |
| `created_at` | TEXT | |
| `expires_at` | TEXT | |
| `revoked` | INTEGER | 0/1，软删除 |

---

## 5. 接口定义

### 5.1 data_service 新增接口

```
POST /api/auth/login
  请求: { "nickname": "...", "password": "..." }
  响应: { "access_token": "...", "user": { "user_id": "...", "display_name": "..." } }
  副作用: Set-Cookie: refresh=...; HttpOnly; Secure; SameSite=Strict

POST /api/auth/refresh
  请求: 无 body（从 cookie 读 refresh token）
  响应: { "access_token": "..." }
  副作用: 轮换 refresh token（旧的标记 revoked，发新的）

POST /api/auth/logout
  请求: 无 body
  响应: { "status": "success" }
  副作用: 删除 cookie，DB 中 refresh token 标记 revoked

GET /api/auth/me
  请求: Authorization: Bearer <access_token>
  响应: { "user_id": "...", "display_name": "...", "nickname": "..." }
```

### 5.2 服务间鉴权

下游服务（langgraph、alert）只接受带 `X-Service-Key: <SERVICE_API_KEY>` 的请求。`SERVICE_API_KEY` 是 data_service 与下游共享的静态密钥，环境变量配置。

---

## 6. 与现有代码的关系

| 现有 | 改动 | 说明 |
|---|---|---|
| `data_layer.py` `create_session`/`session_user`/`revoke_session` | 改为 refresh token 存储 | `token_hash` 改存 `SHA-256(token)` 走索引；加 `revoked` 字段 |
| `data_service/app.py` register/login/logout | 保留，加 JWT 签发 | data_service 兼任中间层，是唯一面向前端的入口 |
| `data_service/app.py` `require_api_token` | 改为用户 JWT 鉴权 | 从静态 API key 改为验 Bearer JWT |
| `app.py`/langgraph | 加服务间鉴权 | 只允许 data_service 调，不直接面向前端 |
| `alert.py` 管理后台 | **不动** | 管理员鉴权与用户 JWT 本就是两套 |
| 前端 `app.js` | 改调 data_service `/api/auth/*` + `/chat` | 不再直连 langgraph |

---

## 7. 安全要点

| 要点 | 做法 | 优先级 |
|---|---|---|
| JWT secret 不进 git | `JWT_SECRET` 环境变量，`.env.example` 只写 key 名 | 必须 |
| Access token 不存 localStorage | 存 JS 变量（刷新页丢失，靠 refresh token 恢复） | 必须 |
| Refresh token 存 httpOnly cookie | `Set-Cookie: refresh=...; HttpOnly; Secure; SameSite=Strict` | 必须 |
| HTTPS only | 生产环境强制，开发用 localhost 豁免 | 生产必须 |
| Token 吊销 | refresh token 存 DB，logout 时标记 revoked；access 靠短命自然过期 | 推荐 |
| CORS | data_service 只允许前端域名 | 必须 |
| 密码哈希 | 现有 `generate_password_hash`（werkzeug）已够用 | 已满足 |

---

## 8. 落地改动清单

/chat 链路跑通后按此清单推进：

| # | 改动 | 负责人 | 层次 | 触及边界 | 状态 |
|---|---|---|---|---|---|
| 1 | data_service 实现 `/api/auth/login` `/api/auth/refresh` `/api/auth/me` `/api/auth/logout`，用 PyJWT 签发/验证 | 王力涵 | L2 | 是（新增接口） | ✅ |
| 2 | `data_layer.py` session 方法改为 refresh token 存储，token_hash 用 SHA-256 | 王力涵 | L0 | 是（DataStore 公共方法） | ✅ |
| 3 | `data_service/app.py` `require_api_token` 改为用户 JWT 鉴权 | 王力涵 | L2 | 是（/api/* 接口） | ✅ |
| 4 | `app.py`/langgraph 加服务间鉴权 | 蒋状钊 | L2 | 是 | ❌ 待蒋状钊 |
| 5 | 前端改调 data_service，token 存内存 + refresh cookie | 袁群 | L3 | 是 | ✅ 测试面板已改 |
| 6 | `API_CONTRACT.md §7` 更新：匿名→JWT 鉴权，补 token 模型和接口定义 | 共同 | 文档 | 是 | ✅ |
| 7 | `.env.example` 加 `JWT_SECRET`、`SERVICE_API_KEY` | 王力涵 | L0 | 是 | ✅ |

---

*文档维护：鉴权方案变更时更新本文档并通知所有对接人。*