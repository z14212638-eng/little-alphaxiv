# 密码找回（邮箱重置）设计

**日期**: 2026-06-29
**状态**: 已批准，待实现
**背景**: 现有登录（`backend/app/routers/auth.py`）是用户名-only，没有"忘记密码"入口；一旦忘记密码且没有旁路，用户在自托管部署里彻底锁死。本设计新增**基于邮箱的密码重置流程** + 一个**管理员 CLI 应急旁路**。

## 决策（已与用户确认）

1. **邮件投递 = SMTP + 控制台兜底**。配置了 `LAX_SMTP_URL` 就发真邮件；没配置就把重置链接打印到后端终端 + 追加到 `backend/lax_reset_links.log`。localhost 零配置可用，生产配 SMTP 即发真邮件。
2. **注册时强制必填邮箱**（新账号）。现有账号邮箱字段为 NULL，需登录后在设置里补填 —— 这是邮箱"以后才填"的固有限制，无法绕过，故同时提供 CLI 旁路。
3. **自动登录**：重置成功后签发新会话直接进 `/`，不再跳回登录页。
4. **管理员 CLI 应急旁路** `tools/reset_password.py`：按用户名直接改库里的密码哈希，不依赖邮箱。解决"我现在就被锁在外面"。

## 1. 端到端流程

- **忘记密码**: 登录页 → "Forgot password?" → `/forgot` → 输入用户名 *或* 邮箱 → `POST /api/auth/forgot-password` → 后端查用户；若有邮箱则生成重置 token 发链接；**无论是否命中都返回 200 + 通用文案**（不泄露账号存在性）。每标识符 60s 冷却防滥用。
- **重置**: 邮件/控制台链接 → `/reset?token=…` → 用户输新密码 → `POST /api/auth/reset-password {token, new_password}` → 后端校验 token、设新哈希、标记 token 已用、**删除该用户所有会话**、签发新会话 → 跳 `/`。
- **投递**: 配了 `LAX_SMTP_URL` 走 SMTP；否则控制台打印 `[lax] password-reset link for <email>: <url>`（测试驱动从这里抓链接）。
- **应急旁路**: `tools/reset_password.py <username>` 交互式设新密码 + 可选清会话。

## 2. 数据模型与迁移（`backend/app/models.py`）

- `User.email: str | None = Field(default=None, unique=True, index=True)` —— 可空（现有账号存活）+ 唯一索引（邮箱→账号一一对应）。SQLite 允许多个 NULL，现有 NULL 行不冲突。
- 新增 `PasswordResetRow`（`__tablename__="password_reset"`）:
  - `id: int` 主键
  - `user_id: int` 外键→`user.id`，`ondelete="CASCADE"`，索引
  - `token_hash: str` 唯一索引 —— **只存 `sha256(token)`，不存明文 token**（泄露库也不能直接用）
  - `expires_at: int`（epoch 秒）
  - `used_at: int | None = None`（单次使用标记）
  - `created_at: int`

Alembic autogenerate 生成一个迁移 `0002_password_reset.py`：给 `user` 加 `email` 列（可空）+ 建 `password_reset` 表。启动时 `lifespan` 自动 `alembic upgrade head`。

## 3. 后端端点与安全（扩展 `backend/app/routers/auth.py` + 新 `backend/app/email.py`）

### `POST /api/auth/forgot-password`
- Body: `{identifier: str}`（用户名或邮箱，normalize 后按 username 精确匹配 *或* email 精确匹配）。
- **总是返回 200** `{"ok": true, "message": "If an account with that identifier exists, a reset link is on its way."}`。
- 命中且有邮箱 → 生成 `token = secrets.token_urlsafe(32)`；**先把这个用户所有未使用 token 标记 used_at**（supersede，只有最新链接有效）；插入新行 `token_hash=sha256(token).hexdigest()`、`expires_at=now+TTL`；调 `email.send_reset_email(...)`。
- 命中但无邮箱 → 不发邮件、不报错、照样返回通用 200（用户需在设置补邮箱或用 CLI）。
- 未命中 → 同样返回通用 200。
- **冷却**: 进程内字典 `{identifier_lower: last_sent_ts}`，60s 内重复请求直接返回通用 200 不再发。冷却只防同一标识符刷邮件，不防分布式的，够用。
- TTL: `LAX_PASSWORD_RESET_TTL_MIN`（默认 30 分钟）。
- 发送失败（SMTP 报错）只 log，不 500（避免泄露发送状态）。

### `POST /api/auth/reset-password`
- Body: `{token: str, new_password: str}`。
- `new_password` 复用 `PASSWORD_MIN=8`。
- 校验: `sha256(token)` 是否匹配某行 → 未过期 → `used_at IS NULL`。
- 成功: 更新 `User.password_hash`；标记该行 `used_at=now`；**删除该 user 所有 `Session` 行**（旧会话立即失效——密码已改，旧 cookie 必须死）；签发新会话；返回 `MeResponse`（前端拿到后跳 `/`）。
- 失败: token 无效/已用 → 401；过期 → 410。统一在 reset 页面显示"链接无效或已过期，请重新申请"。

### `PATCH /api/auth/account`（已认证）
- Body: `{email: str | null}`。
- 校验邮箱格式 + 唯一性（已存在他人邮箱 → 409）。允许置空（清掉邮箱则该账号无法邮件找回，需自担）。
- 给现有账号补邮箱的唯一通道。返回 `{"email": ...}`。

### 新模块 `backend/app/email.py`
- `send_reset_email(to: str, username: str, link: str) -> None`
- 构造纯文本 + HTML 邮件。解析 `LAX_SMTP_URL`:
  - `smtps://user:pass@host:465` → `smtplib.SMTP_SSL`
  - `smtp://user:pass@host:587` → `smtplib.SMTP` + `starttls()`
  - 查询参数 `?starttls=true` 显式开关；`?from=` 覆盖发件人。
- 用 `asyncio.to_thread(...)` 包网络 IO，不阻塞事件循环。
- 未配置 → 控制台后端: `print("[lax] password-reset link for <to>: <link>")` + 追加 `backend/lax_reset_links.log`（一行一个，便于测试抓取）。
- `LAX_SMTP_FROM` 默认取 URL 里的 user。
- 任何异常 `print("[lax] mail send failed: ...")` 吞掉（不 raise，forgot 端点不因它失败）。

### 枚举防护
- forgot 端点对所有分支返回字节级相同的通用文案 + 相同状态码。
- 不暴露"用户不存在"/"该账号没邮箱"。
- 时序: 冷却命中时也走相同返回路径；发送是 `to_thread`，命中/未命中的响应延迟差异主要来自一次 DB 查询，可接受（自托管威胁模型下不强求常量时间）。

## 4. 前端

- **`Login.tsx`**:
  - 登录态密码框下方加 `Forgot password?` 链接 → `/forgot`。
  - 注册表单加必填 **Email** 字段 → `api.register(username, email, password)`。
- **`pages/ForgotPassword.tsx`**（新）: 单输入（用户名或邮箱）+ 提交 → 通用成功文案 + 回登录链接。无错误态（即使乱填也"成功"）。
- **`pages/ResetPassword.tsx`**（新）: 读 `?token=`；缺失/无效显示错误态；否则新密码 + 确认密码 → `api.resetPassword(token, newPassword)` → 成功跳 `/`（已自动登录）。客户端校验长度 + 两次一致。
- **`App.tsx`**: 把 `/forgot`、`/reset` 加入**未认证路由集**（目前只有 `/login` 公开）；已认证时这俩路由 redirect `/`。
- **`lib/api.ts`**:
  - 加 `requestPasswordReset(identifier)`、`resetPassword(token, newPassword)`、`setAccountEmail(email)`。
  - `register(username, email, password)` 签名变更。
  - 顺手修 latent bug: `logout` 里 `"\api\auth\logout"`（反斜杠）→ `"/api/auth/logout"`。
- **`SettingsView.tsx`**: 新增 "Account" 区，显示用户名（只读）+ 可编辑邮箱（现有账号补邮箱的通道）。

## 5. 管理员 CLI 应急旁路 — `tools/reset_password.py`

- 独立脚本，直接用 `bcrypt` + `sqlite3` 操作 `backend/little_alphaxiv.db`，**不依赖 app/Fernet**（改密码哈希不需要解密密钥）。
- `python tools/reset_password.py <username>` → 用 `getpass` 提示新密码（不回显）→ 校验长度 → 更新 `password_hash` → 询问是否清该用户所有 session（可选）→ 打印结果。
- 处理: 用户名不存在 → 报错退出；库不存在 → 指引先跑后端。
- 这是"现在就被锁死"的脱困通道，也覆盖没邮箱的账号。

## 6. 配置 / env（`backend/.env.example`）

新增可选:
```
# --- Password recovery (email reset) ---
# SMTP URL for sending password-reset emails. Unset → reset links are printed
# to the server terminal + appended to backend/lax_reset_links.log (localhost).
#   smtps://user:pass@smtp.gmail.com:465
#   smtp://user:pass@mail.host:587?starttls=true
# LAX_SMTP_URL=
# From: address for reset emails (defaults to the SMTP URL user).
# LAX_SMTP_FROM=
# Reset-link TTL in minutes (default 30).
# LAX_PASSWORD_RESET_TTL_MIN=30
```

## 7. 测试 / 验证

### `tools/drive_password_reset.py`（Playwright，控制台后端抓链接）
覆盖完整链路:
1. 注册带邮箱 → 登录。
2. `/forgot` 提交邮箱 → 从后端终端输出/`lax_reset_links.log` 抓最新链接。
3. 新 Playwright context 打开 `/reset?token=…` → 设新密码 → 断言跳 `/`（自动登录）+ 断言**旧密码现在登录失败**（`POST /api/auth/login` 返回 401）。
4. 断言 token 复用失败（再 POST `/reset-password` 同 token → 401）。
5. 断言 forgot 未命中标识符也返回 200（防枚举回归）。

复用 `drive_auth_persistence.py` 的 `new_context`/注册/`LAX_FRONT`/`LAX_BACK` 模式；用户名按 `e2e_<ts>` 唯一。

### 单元/集成（后端，`backend/tests/` 新目录）
CLAUDE.md 说"no backend tests"，但本特性安全敏感 + 用户强调"做好测试"，故破例新增最小后端测试（`pytest`，可用 `httpx.AsyncClient` + 临时 SQLite）:
- token 单次使用：第二次 reset 同 token → 401。
- 过期 token → 410。
- 重置后该用户所有 Session 被删（旧 cookie 失效）。
- forgot 端点对存在/不存在/无邮箱三种情况返回相同 200 文案。
- `PATCH /api/account` 邮箱唯一性（重复 → 409）、格式校验。
- `email.py` 控制台后端把链接写到 log（解析出 token 正确）。

### 手动 SMTP 核对清单（接真邮件时）
- Gmail: 用应用专用密码，`smtps://<user>:<app-pass>@smtp.gmail.com:465`。
- 验证 From / 收件人 / 链接可点。
- 验证 STARTTLS 主机 `smtp://...:587?starttls=true`。

## 8. 非目标（YAGNI，留待以后）

- 邮箱所有权验证（注册时发验证邮件确认邮箱真属于本人）。当前: 用户可填他人邮箱；自托管/LAN 信任模型下可接受 —— 文档标注。
- 密码强度计 / "不能与旧密码相同"强制。
- 超出"每标识符冷却"的 IP 级限流（如滑动窗口）。
- 重置后通知邮件。

## 9. 安全要点汇总

- 只存 token 哈希，不存明文。
- token 单次使用 + 30min TTL + 新请求 supersede 旧 token。
- 重置成功即删该用户全部 Session（旧 cookie 即死）。
- forgot 端点防枚举（恒定 200 + 通用文案）。
- 密码哈希仍 bcrypt（复用 `security.hash_password`）。
- 邮箱唯一索引保证"邮箱→账号"无歧义。
- 邮件发送失败不泄露发送状态。
