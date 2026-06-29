# 密码找回（邮箱重置）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Little Alphaxiv 加上"忘记密码 → 邮箱重置链接 → 设新密码自动登录"流程 + 管理员 CLI 应急旁路，让被锁死的账号能自救。

**Architecture:** 复用现有 `routers/auth.py` + `security.py`（bcrypt/itsdangerous）。新增 `password_reset` 表存 token 哈希（单次使用 + TTL + supersede），`User.email` 列（可空唯一）。邮件投递 `email.py`：配了 `LAX_SMTP_URL` 走 SMTP，否则控制台 + log 文件。重置成功即删该用户全部 Session（旧 cookie 即死）并签发新会话。

**Tech Stack:** FastAPI + SQLModel + Alembic + aiosqlite（后端）；React + react-router + zustand（前端）；`smtplib`（标准库）；`pytest` + `httpx.AsyncClient`（后端测试，新引入）；Playwright（E2E）。

## Global Constraints

- 后端 Python 3.10+（PEP 604 `str | None`），用 `Agent_env` conda 环境。
- 密码最小长度 `PASSWORD_MIN = 8`（`routers/auth.py` 已定义，复用）。
- token 只存 `sha256(token).hexdigest()`，永不存明文。
- 重置 token TTL 默认 30 分钟，可由 `LAX_PASSWORD_RESET_TTL_MIN` 覆盖。
- forgot 端点对所有分支返回字节级相同的 200 + 通用文案（防枚举）。
- 邮箱列可空（现有账号存活）+ 唯一索引（SQLite 允许多 NULL）。
- 迁移 `0002_password_reset.py`，启动时 `lifespan` 自动 `alembic upgrade head`。
- 前端无 lint 脚本，`npm run typecheck` 是门禁；`npm test`（Vitest）跑前端测试。
- 工作在 worktree（`.claude/worktrees/`）里完成，`frontend/node_modules` 是 junction。
- `mock_llm.py` 监听 `"title generator"` + `"paper being discussed"` 词组——本计划不动 title 提示词。

---

## File Structure

**后端**
- Modify `backend/app/models.py` — `User.email` 列 + 新 `PasswordResetRow` 表。
- Create `backend/alembic/versions/0002_password_reset.py` — 迁移。
- Create `backend/app/email.py` — `send_reset_email()`，SMTP/控制台双后端。
- Modify `backend/app/routers/auth.py` — 加 5 个端点（forgot / reset / account PATCH / register 带 email / me 带 email）。
- Modify `backend/.env.example` — 3 个新可选变量。
- Create `backend/tests/conftest.py` + `backend/tests/test_password_reset.py` — 后端测试（新引入 pytest）。
- Modify `backend/requirements.txt` — 加 `pytest`、`pytest-asyncio`、`httpx`（httpx 已是依赖，确认即可）。

**前端**
- Modify `frontend/src/lib/api.ts` — `register` 改签名，加 3 个函数，修 `logout` 反斜杠 bug。
- Create `frontend/src/pages/ForgotPassword.tsx`。
- Create `frontend/src/pages/ResetPassword.tsx`。
- Modify `frontend/src/pages/Login.tsx` — 注册加 Email 字段 + 忘记密码链接。
- Modify `frontend/src/App.tsx` — `/forgot` `/reset` 公开路由。
- Modify `frontend/src/views/SettingsView.tsx` — Account 区（用户名 + 邮箱）。
- Modify `frontend/src/index.css` — 复用 `.login-*` 样式，少量新增。

**工具**
- Create `tools/reset_password.py` — 管理员 CLI。
- Create `tools/drive_password_reset.py` — Playwright E2E。

---

## Task 1: 数据模型 + 迁移（User.email + PasswordResetRow）

**Files:**
- Modify: `backend/app/models.py`（`User` 类 + 文件末尾加 `PasswordResetRow`）
- Create: `backend/alembic/versions/0002_password_reset.py`

**Interfaces:**
- Produces: `User.email: str | None`（可空、唯一索引）；`PasswordResetRow` 模型（字段见下）。后续任务的端点代码直接 import 这两个。

- [ ] **Step 1: 修改 `models.py` 的 `User` 类加 email 列**

在 `backend/app/models.py` 的 `User` 类里，`password_hash` 行后加：

```python
class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    # username stored lowercased+trimmed on insert; unique index for fast lookup
    username: str = Field(unique=True, index=True)
    password_hash: str  # bcrypt
    # Email for password recovery. Nullable so pre-migration accounts survive;
    # unique so email→account is unambiguous (SQLite allows multiple NULLs).
    email: str | None = Field(default=None, unique=True, index=True)
    created_at: int = Field(default_factory=_now)
```

- [ ] **Step 2: 在 `models.py` 末尾加 `PasswordResetRow`**

在文件末尾追加：

```python
# ---------------------------------------------------------------------------
# Password reset tokens (hashed, single-use, TTL-bounded)
# ---------------------------------------------------------------------------


class PasswordResetRow(SQLModel, table=True):
    """A single-use password-reset token. Only sha256(token) is stored — the
    plaintext token exists only in the reset link sent to the user. A new
    request supersedes the user's prior unused tokens (marked used_at)."""
    __tablename__ = "password_reset"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    token_hash: str = Field(unique=True, index=True)  # sha256(token).hexdigest()
    expires_at: int  # epoch seconds
    used_at: int | None = None  # set when consumed → single-use
    created_at: int = Field(default_factory=_now)
```

- [ ] **Step 3: 生成 Alembic 迁移文件 `0002_password_reset.py`**

手动创建 `backend/alembic/versions/0002_password_reset.py`（不依赖 autogenerate，避免环境差异）：

```python
"""add email + password_reset table

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # user.email — nullable + unique (SQLite allows multiple NULLs).
    op.add_column("user", sa.Column("email", sa.String, nullable=True))
    op.create_index("ix_user_email", "user", ["email"], unique=True)

    # password_reset — hashed single-use tokens.
    op.create_table(
        "password_reset",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String, nullable=False),
        sa.Column("expires_at", sa.Integer, nullable=False),
        sa.Column("used_at", sa.Integer, nullable=True),
        sa.Column("created_at", sa.Integer, nullable=False),
    )
    op.create_index("ix_password_reset_user_id", "password_reset", ["user_id"])
    op.create_index("ix_password_reset_token_hash", "password_reset", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_password_reset_token_hash", table_name="password_reset")
    op.drop_index("ix_password_reset_user_id", table_name="password_reset")
    op.drop_table("password_reset")
    op.drop_index("ix_user_email", table_name="user")
    op.drop_column("user", "email")
```

- [ ] **Step 4: 验证迁移可干净应用（空 DB 上 upgrade head）**

Run（在 `backend/` 下，`Agent_env`）:
```
conda activate Agent_env
cd backend
python -c "import os; os.environ.setdefault('LAX_DATABASE_URL','sqlite:///./_tmp_migrate.db'); from app.db import init_db; import asyncio; asyncio.run(init_db())" 
python -c "from alembic.config import Config; from alembic import command; c=Config('alembic.ini'); c.set_main_option('script_location','alembic'); command.upgrade(c,'head')"
python -c "import sqlite3; con=sqlite3.connect('_tmp_migrate.db'); print(sorted(r[0] for r in con.execute(\"select name from sqlite_master where type='table'\"))); print('email col:', [r for r in con.execute('pragma table_info(user)') if r[1]=='email'])"
del _tmp_migrate.db
```
Expected: 表列表含 `password_reset`；`user` 表有 `email` 列。

- [ ] **Step 5: 提交**

```bash
git add backend/app/models.py backend/alembic/versions/0002_password_reset.py
git commit -m "feat(db): add User.email + password_reset table (migration 0002)"
```

---

## Task 2: `email.py` 邮件模块（SMTP + 控制台后端）

**Files:**
- Create: `backend/app/email.py`

**Interfaces:**
- Produces: `send_reset_email(to: str, username: str, link: str) -> None`。配置了 `LAX_SMTP_URL` 走 SMTP（`asyncio.to_thread` 包裹），否则打印到终端 + 追加 `backend/lax_reset_links.log`。永不 raise（异常只 log）。
- Consumes: 环境变量 `LAX_SMTP_URL`、`LAX_SMTP_FROM`。

- [ ] **Step 1: 写 `email.py`**

```python
"""Password-reset email delivery.

Two backends, chosen by config:
  * SMTP   — when LAX_SMTP_URL is set (e.g. smtps://user:pass@host:465).
  * Console — otherwise: print the link + append to backend/lax_reset_links.log
    (zero-config for localhost; the E2E driver scrapes the link from here).

Never raises: a send failure is logged and swallowed so the forgot-password
endpoint can't leak send state or 500. Network IO runs in a worker thread via
asyncio.to_thread so the event loop isn't blocked.
"""
from __future__ import annotations

import asyncio
import os
import urllib.parse
from email.message import EmailMessage
from pathlib import Path

import smtplib

_LOG_PATH = Path(__file__).resolve().parent.parent / "lax_reset_links.log"


def _parse_smtp_url(url: str) -> dict:
    """Parse smtp(s)://user:pass@host:port[?starttls=true&from=...]."""
    p = urllib.parse.urlsplit(url)
    scheme = p.scheme.lower()
    use_ssl = scheme == "smtps"
    starttls = False
    from_addr: str | None = None
    for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True):
        if k.lower() == "starttls" and v.lower() in ("1", "true", "yes"):
            starttls = True
        if k.lower() == "from":
            from_addr = v
    userinfo = urllib.parse.unquote(p.username) if p.username else ""
    password = urllib.parse.unquote(p.password) if p.password else ""
    if not from_addr and userinfo:
        from_addr = userinfo
    if not p.hostname or not p.port:
        raise ValueError(f"invalid SMTP url: {url}")
    return {
        "host": p.hostname, "port": p.port, "use_ssl": use_ssl,
        "starttls": starttls or (scheme == "smtp" and p.port == 587 and not use_ssl and not _explicit_no_starttls(url)),
        "username": userinfo, "password": password, "from_addr": from_addr,
    }


def _explicit_no_starttls(url: str) -> bool:
    return "starttls=false" in url.lower()


def _build_message(to: str, username: str, link: str, from_addr: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = "Little Alphaxiv — reset your password"
    body_text = (
        f"Hi {username},\n\n"
        f"Someone (hopefully you) requested a password reset for your Little "
        f"Alphaxiv account.\n\n"
        f"Reset link (expires in 30 minutes):\n{link}\n\n"
        f"If you didn't request this, ignore this email — your password stays "
        f"unchanged.\n"
    )
    msg.set_content(body_text)
    msg.add_alternative(
        f"<html><body><p>Hi {username},</p>"
        f"<p>Someone (hopefully you) requested a password reset for your "
        f"Little Alphaxiv account.</p>"
        f"<p><a href=\"{link}\">Reset your password</a> "
        f"(expires in 30 minutes).</p>"
        f"<p>If you didn't request this, ignore this email — your password "
        f"stays unchanged.</p></body></html>",
        subtype="html",
    )
    return msg


def _send_smtp_sync(cfg: dict, to: str, username: str, link: str) -> None:
    from_addr = cfg["from_addr"] or cfg["username"]
    if not from_addr:
        raise ValueError("no from address: set LAX_SMTP_FROM")
    msg = _build_message(to, username, link, from_addr)
    if cfg["use_ssl"]:
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20)
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=20)
    try:
        server.ehlo()
        if cfg["starttls"]:
            server.starttls()
            server.ehlo()
        if cfg["username"]:
            server.login(cfg["username"], cfg["password"])
        server.send_message(msg)
    finally:
        server.quit()


def _send_console_sync(to: str, username: str, link: str) -> None:
    line = f"[lax] password-reset link for {to} (user={username}): {link}"
    print(line, flush=True)
    try:
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        print(f"[lax] failed to write reset link log: {exc}", flush=True)


async def send_reset_email(to: str, username: str, link: str) -> None:
    """Send the reset link. SMTP if configured, else console. Never raises."""
    url = os.environ.get("LAX_SMTP_URL", "").strip()
    try:
        if not url:
            await asyncio.to_thread(_send_console_sync, to, username, link)
            return
        cfg = _parse_smtp_url(url)
        override = os.environ.get("LAX_SMTP_FROM", "").strip()
        if override:
            cfg["from_addr"] = override
        await asyncio.to_thread(_send_smtp_sync, cfg, to, username, link)
    except Exception as exc:  # noqa: BLE001 — must not break the forgot endpoint
        print(f"[lax] password-reset email send failed: {exc}", flush=True)
```

- [ ] **Step 2: 提交**

```bash
git add backend/app/email.py
git commit -m "feat(email): add password-reset email module (SMTP + console backend)"
```

---

## Task 3: 后端测试脚手架（conftest + pytest 配置）

**Files:**
- Create: `backend/tests/conftest.py`
- Modify: `backend/requirements.txt`（加 `pytest`、`pytest-asyncio`）
- Create: `backend/pytest.ini`

**Interfaces:**
- Produces: `conftest.py` 暴露 `client` fixture（`httpx.AsyncClient` 指向临时 SQLite 的 app）+ `db_path` fixture。后续后端测试任务都用它。

- [ ] **Step 1: 加测试依赖**

在 `backend/requirements.txt` 末尾加（httpx 已在）:
```
pytest>=8.0
pytest-asyncio>=0.23
```

安装:
```
conda activate Agent_env
cd backend
pip install pytest pytest-asyncio
```

- [ ] **Step 2: 写 `pytest.ini`**

`backend/pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 3: 写 `tests/conftest.py`**

```python
"""Shared fixtures: a fresh app + temp SQLite per test (isolated)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Must be set before importing app.* (db reads LAX_DATABASE_URL at import).
@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    # Use the file directly (no wal_rewrite concerns at import time).
    os.environ["LAX_DATABASE_URL"] = f"sqlite:///{db_file}"
    monkeypatch.setenv("LAX_DATABASE_URL", f"sqlite:///{db_file}")
    yield db_file
    os.environ.pop("LAX_DATABASE_URL", None)


@pytest_asyncio.fixture
async def client():
    # Re-init security + create schema fresh against the temp DB.
    from app import db as dbmod, security
    # close any prior engine from a previous test
    await dbmod.close_db()
    # rebuild engine pointing at the new temp url
    dbmod.engine = dbmod.create_async_engine(
        os.environ["LAX_DATABASE_URL"].replace("sqlite:///", "sqlite+aiosqlite:///"),
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    @__import__("sqlalchemy").event.listens_for(dbmod.engine.sync_engine, "connect")
    def _pragmas(conn, _):  # noqa: ANN001
        c = conn.cursor()
        for stmt in ("PRAGMA journal_mode=WAL", "PRAGMA foreign_keys=ON"):
            c.execute(stmt)
        c.close()
    from sqlmodel import SQLModel
    import app.models  # noqa: F401 — register tables
    async with dbmod.engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    security.init_security()

    from app.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await dbmod.close_db()
```

> 注: `dbmod.engine` 重建是为了让每个测试用独立的临时文件。`SQLModel.metadata.create_all` 在测试里直接建表（绕过 alembic，因为 alembic 在 `lifespan` 里跑，而 `AsyncClient` 的 ASGI transport 会触发 lifespan）。

- [ ] **Step 4: 验证测试骨架可空跑**

```
cd backend
python -m pytest tests/ -v
```
Expected: "no tests ran"（无误）或 0 错误收集。

- [ ] **Step 5: 提交**

```bash
git add backend/requirements.txt backend/pytest.ini backend/tests/conftest.py
git commit -m "test(backend): add pytest scaffold (temp-db client fixture)"
```

---

## Task 4: forgot-password 端点（TDD）

**Files:**
- Modify: `backend/app/routers/auth.py`
- Test: `backend/tests/test_password_reset.py`

**Interfaces:**
- Consumes: `User`（Task 1）、`PasswordResetRow`（Task 1）、`email.send_reset_email`（Task 2）。
- Produces: `POST /api/auth/forgot-password` body `{identifier}` → 200 `{"ok": True, "message": ...}`；副作用：写 `password_reset` 行 + 发邮件（控制台）。

- [ ] **Step 1: 写失败测试（防枚举 + 生成 token 行）**

`backend/tests/test_password_reset.py`:
```python
"""Password-reset endpoint tests."""
from __future__ import annotations

import hashlib
import re

from sqlmodel import select

from app.models import PasswordResetRow


GENERIC = "If an account with that identifier exists, a reset link is on its way."


async def _register(client, username="alice", email="alice@example.com", password="password123"):
    r = await client.post("/api/auth/register", json={"username": username, "email": email, "password": password})
    assert r.status_code == 201, r.text
    return r


async def test_forgot_returns_generic_for_unknown_identifier(client):
    r = await client.post("/api/auth/forgot-password", json={"identifier": "ghost"})
    assert r.status_code == 200
    assert r.json()["message"] == GENERIC


async def test_forgot_creates_token_row_and_supersedes(client):
    await _register(client)
    r1 = await client.post("/api/auth/forgot-password", json={"identifier": "alice"})
    assert r1.status_code == 200 and r1.json()["message"] == GENERIC
    r2 = await client.post("/api/auth/forgot-password", json={"identifier": "alice@example.com"})
    assert r2.status_code == 200
    # two rows, but the first must be marked used (superseded).
    async with client._app_fixture_session() as s:  # placeholder — replaced below
        rows = (await s.exec(select(PasswordResetRow).where(PasswordResetRow.user_id == 1))).all()
    hashes = {row.token_hash for row in rows}
    assert len(hashes) == 2
    used = [r for r in rows if r.used_at is not None]
    assert len(used) == 1  # the older token was superseded
```

> 注: `client._app_fixture_session()` 占位——Step 3 改为直接用 `dbmod.async_session_factory()`。先保留测试骨架以驱动接口。

- [ ] **Step 2: 跑测试确认失败**

```
cd backend
python -m pytest tests/test_password_reset.py -v
```
Expected: FAIL（端点不存在 / `_app_fixture_session` 不存在）。

- [ ] **Step 3: 实现端点（修测试里的 session 取法）**

在 `backend/app/routers/auth.py` 顶部 import 后加:
```python
import hashlib
import secrets

from ..email import send_reset_email
from ..models import PasswordResetRow
```

把 `Credentials` 改为带 email（注册用），并在文件中部加常量与冷却字典:
```python
USERNAME_MIN = 3
PASSWORD_MIN = 8

# In-process cooldown: {identifier_lower: last_sent_epoch}. Prevents one client
# from spamming reset emails for the same identifier. Not distributed.
_forgot_cooldown: dict[str, int] = {}
FORGOT_COOLDOWN_S = 60


def _reset_ttl_seconds() -> int:
    return int(os.environ.get("LAX_PASSWORD_RESET_TTL_MIN", "30")) * 60


GENERIC_FORGOT_MSG = (
    "If an account with that identifier exists, a reset link is on its way."
)
```

`os` 需 import（在文件顶部 `import time` 旁加 `import os`）。

把 `register` 端点的 `Credentials` 用法改为新模型:
```python
class Credentials(BaseModel):
    username: str = Field(min_length=USERNAME_MIN)
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN)
```
需 `from pydantic import BaseModel, EmailStr, Field`。EmailStr 需要 `email-validator` 包 —— 加到 `requirements.txt`（`email-validator>=2.0`）。

`register` 端点改:
```python
@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=MeResponse)
async def register(
    creds: Credentials,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> MeResponse:
    username = creds.username.strip().lower()
    if len(username) < USERNAME_MIN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "username too short")
    existing = (
        await session.exec(select(User).where(User.username == username))
    ).first()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "username taken")
    email_norm = creds.email.strip().lower()
    email_taken = (
        await session.exec(select(User).where(User.email == email_norm))
    ).first()
    if email_taken is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    user = User(username=username, email=email_norm, password_hash=security.hash_password(creds.password))
    session.add(user)
    await session.commit()
    await session.refresh(user)
    await _issue_session(response, session, user)
    return MeResponse(id=user.id, username=user.username, hasData=False)
```

`MeResponse` 加 email:
```python
class MeResponse(BaseModel):
    id: int
    username: str
    email: str | None = None
    hasData: bool
```
`me` 与 `login` 端点的返回也带上 `email=user.email`。

`login` 端点保持（用户名+密码登录），返回 `MeResponse(id=user.id, username=user.username, email=user.email, hasData=has_data)`。

加 forgot 端点:
```python
class ForgotBody(BaseModel):
    identifier: str


@router.post("/forgot-password")
async def forgot_password(
    body: ForgotBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    ident = body.identifier.strip().lower()
    now = int(time.time())
    # Cooldown: same identifier can't trigger a second send within 60s.
    last = _forgot_cooldown.get(ident, 0)
    if now - last < FORGOT_COOLDOWN_S:
        return {"ok": True, "message": GENERIC_FORGOT_MSG}
    _forgot_cooldown[ident] = now

    user = (
        await session.exec(
            select(User).where((User.username == ident) | (User.email == ident))
        )
    ).first()
    if user is None or not user.email:
        # No user, or no email on file → still return generic. No token created.
        return {"ok": True, "message": GENERIC_FORGOT_MSG}

    # Supersede this user's prior unused tokens.
    prior = (
        await session.exec(
            select(PasswordResetRow).where(
                PasswordResetRow.user_id == user.id,
                PasswordResetRow.used_at.is_(None),
            )
        )
    ).all()
    for row in prior:
        row.used_at = now

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    reset_row = PasswordResetRow(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=now + _reset_ttl_seconds(),
    )
    session.add(reset_row)
    await session.commit()

    link = _build_reset_link(token)
    await send_reset_email(user.email, user.username, link)
    return {"ok": True, "message": GENERIC_FORGOT_MSG}
```

加 `_build_reset_link`（构造前端 `/reset?token=` URL；origin 来自 `LAX_ALLOWED_ORIGINS` 第一个或 `http://127.0.0.1:5173`）:
```python
def _build_reset_link(token: str) -> str:
    origin = os.environ.get("LAX_ALLOWED_ORIGINS", "http://127.0.0.1:5173").split(",")[0].strip()
    return f"{origin.rstrip('/')}/reset?token={token}"
```

- [ ] **Step 4: 修测试里 session 取法，跑测试**

把 `client._app_fixture_session()` 替换为直接用 factory:
```python
from app import db as dbmod

async def test_forgot_creates_token_row_and_supersedes(client):
    await _register(client)
    await client.post("/api/auth/forgot-password", json={"identifier": "alice"})
    await client.post("/api/auth/forgot-password", json={"identifier": "alice@example.com"})
    async with dbmod.async_session_factory() as s:
        rows = (await s.exec(select(PasswordResetRow).where(PasswordResetRow.user_id == 1))).all()
    assert len(rows) == 2
    assert sum(1 for r in rows if r.used_at is not None) == 1
```

跑:
```
cd backend
python -m pytest tests/test_password_reset.py -v
```
Expected: forgot 两个测试 PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/routers/auth.py backend/tests/test_password_reset.py backend/requirements.txt
git commit -m "feat(auth): forgot-password endpoint (anti-enumeration + supersede tokens)"
```

---

## Task 5: reset-password 端点（TDD）

**Files:**
- Modify: `backend/app/routers/auth.py`
- Test: `backend/tests/test_password_reset.py`（追加）

**Interfaces:**
- Produces: `POST /api/auth/reset-password` body `{token, new_password}` → 成功 200 `MeResponse` + Set-Cookie；失败 401（无效/已用）/ 410（过期）。副作用：删该用户全部 Session、设新密码哈希、token 标 used。

- [ ] **Step 1: 写失败测试（成功重置 + 单次使用 + 删会话 + 旧密码失效）**

追加到 `tests/test_password_reset.py`:
```python
async def _grab_reset_link(client, identifier="alice"):
    # Console backend writes to backend/lax_reset_links.log; read the last line.
    from app.email import _LOG_PATH
    await client.post("/api/auth/forgot-password", json={"identifier": identifier})
    lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()
    m = re.search(r"(https?://\S+/reset\?token=\S+)", lines[-1])
    assert m, f"no reset link in log: {lines[-3:]}"
    return m.group(1)


async def test_reset_succeeds_and_invalidates_old_password(client):
    await _register(client, username="bob", email="bob@example.com", password="oldpass123")
    # old password works
    r0 = await client.post("/api/auth/login", json={"username": "bob", "password": "oldpass123"})
    assert r0.status_code == 200
    link = await _grab_reset_link(client, "bob")
    token = link.split("token=")[1]
    # reset
    r = await client.post("/api/auth/reset-password", json={"token": token, "new_password": "brandnew9"})
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "bob"
    # token single-use: reuse → 401
    r2 = await client.post("/api/auth/reset-password", json={"token": token, "new_password": "another11"})
    assert r2.status_code == 401
    # old password now fails
    r3 = await client.post("/api/auth/login", json={"username": "bob", "password": "oldpass123"})
    assert r3.status_code == 401
    # new password works
    r4 = await client.post("/api/auth/login", json={"username": "bob", "password": "brandnew9"})
    assert r4.status_code == 200


async def test_reset_expired_token_returns_410(client):
    await _register(client, username="carol", email="carol@example.com", password="pass1234")
    link = await _grab_reset_link(client, "carol")
    token = link.split("token=")[1]
    # Expire it manually by backdating the row.
    import time as _t
    from app import db as dbmod
    async with dbmod.async_session_factory() as s:
        from sqlmodel import select
        from app.models import PasswordResetRow
        row = (await s.exec(select(PasswordResetRow).where(PasswordResetRow.token_hash != ""))).first()
        row.expires_at = int(_t.time()) - 1
        s.add(row); await s.commit()
    r = await client.post("/api/auth/reset-password", json={"token": token, "new_password": "newpass12"})
    assert r.status_code == 410


async def test_reset_invalidates_all_sessions(client):
    await _register(client, username="dave", email="dave@example.com", password="pass1234")
    # login creates a session (cookie jar carries it)
    await client.post("/api/auth/login", json={"username": "dave", "password": "pass1234"})
    me1 = await client.get("/api/auth/me")
    assert me1.status_code == 200
    link = await _grab_reset_link(client, "dave")
    token = link.split("token=")[1]
    await client.post("/api/auth/reset-password", json={"token": token, "new_password": "newpass12"})
    # old session cookie must now be invalid
    me2 = await client.get("/api/auth/me")
    assert me2.status_code == 401
```

- [ ] **Step 2: 跑测试确认失败**

```
python -m pytest tests/test_password_reset.py -v
```
Expected: 三个 reset 测试 FAIL（端点不存在）。

- [ ] **Step 3: 实现 reset 端点**

在 `auth.py` 加:
```python
class ResetBody(BaseModel):
    token: str
    new_password: str = Field(min_length=PASSWORD_MIN)


@router.post("/reset-password", response_model=MeResponse)
async def reset_password(
    body: ResetBody,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> MeResponse:
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    row = (
        await session.exec(
            select(PasswordResetRow).where(PasswordResetRow.token_hash == token_hash)
        )
    ).first()
    now = int(time.time())
    if row is None or row.used_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or used reset token")
    if row.expires_at < now:
        raise HTTPException(status.HTTP_410_GONE, "reset token expired")
    user = await session.get(User, row.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid reset token")
    user.password_hash = security.hash_password(body.new_password)
    row.used_at = now
    # Invalidate ALL of this user's sessions (old cookies die immediately).
    old_sessions = (
        await session.exec(select(Session).where(Session.user_id == user.id))
    ).all()
    for s in old_sessions:
        await session.delete(s)
    await session.commit()
    await _issue_session(response, session, user)
    has_data = await _user_has_data(session, user.id)
    return MeResponse(id=user.id, username=user.username, email=user.email, hasData=has_data)
```

`select(Session)` 需要 `Session` 已在 import（文件顶部 `from ..models import ... Session, User` 已有）。

- [ ] **Step 4: 跑测试确认通过**

```
python -m pytest tests/test_password_reset.py -v
```
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/routers/auth.py backend/tests/test_password_reset.py
git commit -m "feat(auth): reset-password endpoint (single-use, expiry, session purge)"
```

---

## Task 6: account PATCH 端点（设/改邮箱，TDD）

**Files:**
- Modify: `backend/app/routers/auth.py`
- Test: `backend/tests/test_password_reset.py`（追加）

**Interfaces:**
- Produces: `PATCH /api/auth/account` body `{email: str | null}`（已认证）→ 200 `{"email": str|null}`。唯一性 409、格式 422（pydantic）。

- [ ] **Step 1: 写失败测试**

追加:
```python
async def test_set_account_email_unique(client):
    await _register(client, username="eve", email="eve@example.com", password="pass1234")
    await _register(client, username="frank", email="frank@example.com", password="pass1234")
    # eve tries to take frank's email → 409
    r = await client.patch("/api/auth/account", json={"email": "frank@example.com"})
    assert r.status_code == 409
    # eve changes own email → ok
    r2 = await client.patch("/api/auth/account", json={"email": "eve2@example.com"})
    assert r2.status_code == 200
    assert r2.json()["email"] == "eve2@example.com"


async def test_account_requires_auth(client):
    r = await client.patch("/api/auth/account", json={"email": "x@example.com"})
    assert r.status_code == 401
```

- [ ] **Step 2: 跑确认失败**

```
python -m pytest tests/test_password_reset.py -k account -v
```
Expected: FAIL。

- [ ] **Step 3: 实现端点**

在 `auth.py` 加（`current_user` 已 import 自 `..deps`）:
```python
class AccountBody(BaseModel):
    email: str | None = None


@router.patch("/account")
async def update_account(
    body: AccountBody,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if body.email is None:
        user.email = None
    else:
        email_norm = body.email.strip().lower()
        if "@" not in email_norm or "." not in email_norm.split("@")[-1]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid email")
        taken = (
            await session.exec(
                select(User).where(User.email == email_norm, User.id != user.id)
            )
        ).first()
        if taken is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
        user.email = email_norm
    session.add(user)
    await session.commit()
    return {"email": user.email}
```

- [ ] **Step 4: 跑确认通过**

```
python -m pytest tests/test_password_reset.py -v
```
Expected: 全部 PASS（含 account 测试）。

- [ ] **Step 5: 更新 `.env.example`**

在 `backend/.env.example` 末尾加:
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

- [ ] **Step 6: 提交**

```bash
git add backend/app/routers/auth.py backend/tests/test_password_reset.py backend/.env.example
git commit -m "feat(auth): PATCH /account email + env docs"
```

---

## Task 7: 前端 api.ts + Login.tsx（注册带 email + 忘记密码链接）

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/pages/Login.tsx`

**Interfaces:**
- Produces: `register(username, email, password)`、`requestPasswordReset(identifier)`、`resetPassword(token, newPassword)`、`setAccountEmail(email)`、`getAccount()`。

- [ ] **Step 1: 改 `api.ts`**

在 `Me` interface 加 `email`:
```ts
export interface Me {
  id: number;
  username: string;
  email: string | null;
  hasData: boolean;
}
```

`register` 改签名:
```ts
export async function register(username: string, email: string, password: string): Promise<Me> {
  const r = await jfetch("/api/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, email, password }),
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}
```

修 `logout` 反斜杠 bug（`"\api\auth\logout"` → `"/api/auth/logout"`）+ 追加新函数（文件末尾 auth 段）:
```ts
export async function logout(): Promise<void> {
  await jfetch("/api/auth/logout", { method: "POST" });
}

export async function requestPasswordReset(identifier: string): Promise<void> {
  await jfetch("/api/auth/forgot-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier }),
  });
}

export async function resetPassword(token: string, newPassword: string): Promise<Me> {
  const r = await jfetch("/api/auth/reset-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, new_password: newPassword }),
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}

export async function setAccountEmail(email: string | null): Promise<{ email: string | null }> {
  const r = await jfetch("/api/auth/account", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!r.ok) throw new Error(await errText(r));
  return r.json();
}
```

- [ ] **Step 2: 改 `Login.tsx` 加 Email 字段 + 忘记密码链接**

替换整个 `Login.tsx`:
```tsx
// Login / Register page. On success the backend sets the httpOnly lax_session
// cookie; we hard-navigate to "/" so App's boot re-runs with the cookie.

import { useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../lib/api";

export default function Login() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const u = username.trim();
    if (u.length < 3) { setError("Username must be at least 3 characters."); return; }
    if (password.length < 8) { setError("Password must be at least 8 characters."); return; }
    if (mode === "register") {
      const em = email.trim();
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(em)) { setError("Enter a valid email."); return; }
      setBusy(true);
      try {
        await api.register(u, em, password);
        window.location.assign("/");
        return;
      } catch (err) {
        setError((err as Error).message || "Registration failed.");
        setBusy(false);
        return;
      }
    }
    setBusy(true);
    try {
      await api.login(u, password);
      window.location.assign("/");
    } catch (err) {
      setError((err as Error).message || "Authentication failed.");
      setBusy(false);
    }
  }

  return (
    <main className="main-pane login-pane">
      <form className="login-card" onSubmit={submit}>
        <h1>Little Alphaxiv</h1>
        <p className="login-sub">
          {mode === "login" ? "Sign in to your account" : "Create an account"}
        </p>
        <label className="login-field">
          <span>Username</span>
          <input type="text" value={username} autoComplete="username"
            onChange={(e) => setUsername(e.target.value)} disabled={busy} autoFocus />
        </label>
        {mode === "register" && (
          <label className="login-field">
            <span>Email</span>
            <input type="email" value={email} autoComplete="email"
              onChange={(e) => setEmail(e.target.value)} disabled={busy} />
          </label>
        )}
        <label className="login-field">
          <span>Password</span>
          <input type="password" value={password}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            onChange={(e) => setPassword(e.target.value)} disabled={busy} />
        </label>
        {error && <div className="login-error">{error}</div>}
        <button type="submit" className="login-submit" disabled={busy}>
          {busy ? "…" : mode === "login" ? "Sign in" : "Register"}
        </button>
        {mode === "login" && (
          <Link to="/forgot" className="login-toggle">Forgot password?</Link>
        )}
        <button type="button" className="login-toggle"
          onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(null); }}
          disabled={busy}>
          {mode === "login" ? "Need an account? Register" : "Already have an account? Sign in"}
        </button>
        <p className="login-hint">
          Your chat history, annotations, and provider keys are stored on the
          server (keys encrypted at rest), tied to this account — so switching
          browsers just means signing back in.
        </p>
      </form>
    </main>
  );
}
```

- [ ] **Step 3: typecheck**

```
cd frontend
npm run typecheck
```
Expected: 0 errors.

- [ ] **Step 4: 提交**

```bash
git add frontend/src/lib/api.ts frontend/src/pages/Login.tsx
git commit -m "feat(ui): register w/ email + forgot-password link; fix logout path"
```

---

## Task 8: ForgotPassword + ResetPassword 页面 + App 路由

**Files:**
- Create: `frontend/src/pages/ForgotPassword.tsx`
- Create: `frontend/src/pages/ResetPassword.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Produces: 两个公开路由页面。

- [ ] **Step 1: 写 `ForgotPassword.tsx`**

```tsx
// Forgot-password page: submit username or email. The backend ALWAYS returns a
// generic success (anti-enumeration), so we show the same message regardless.

import { useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../lib/api";

export default function ForgotPassword() {
  const [identifier, setIdentifier] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!identifier.trim()) return;
    setBusy(true);
    try {
      await api.requestPasswordReset(identifier.trim());
      setDone(true);
    } catch {
      setDone(true); // backend still returns 200; show generic success
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="main-pane login-pane">
      <form className="login-card" onSubmit={submit}>
        <h1>Reset password</h1>
        <p className="login-sub">Enter your username or email.</p>
        {done ? (
          <p className="login-hint">
            If an account with that identifier exists, a reset link is on its
            way. Check your email (or the server terminal, if no SMTP is
            configured).
          </p>
        ) : (
          <>
            <label className="login-field">
              <span>Username or email</span>
              <input type="text" value={identifier} autoFocus
                onChange={(e) => setIdentifier(e.target.value)} disabled={busy} />
            </label>
            <button type="submit" className="login-submit" disabled={busy}>
              {busy ? "…" : "Send reset link"}
            </button>
          </>
        )}
        <Link to="/login" className="login-toggle">Back to sign in</Link>
      </form>
    </main>
  );
}
```

- [ ] **Step 2: 写 `ResetPassword.tsx`**

```tsx
// Reset-password page: opened from the email link /reset?token=…

import { useState } from "react";
import { useSearchParams, Navigate, Link } from "react-router-dom";
import * as api from "../lib/api";

export default function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!token) {
    return (
      <main className="main-pane login-pane">
        <div className="login-card">
          <h1>Invalid link</h1>
          <p className="login-hint">This reset link is missing a token.</p>
          <Link to="/forgot" className="login-toggle">Request a new link</Link>
        </div>
      </main>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (pw.length < 8) { setError("Password must be at least 8 characters."); return; }
    if (pw !== pw2) { setError("Passwords do not match."); return; }
    setBusy(true);
    try {
      await api.resetPassword(token, pw);
      // backend set the session cookie → boot as authenticated.
      window.location.assign("/");
    } catch (err) {
      setError((err as Error).message || "Reset failed. The link may be invalid or expired.");
      setBusy(false);
    }
  }

  return (
    <main className="main-pane login-pane">
      <form className="login-card" onSubmit={submit}>
        <h1>Set a new password</h1>
        <p className="login-sub">Choose a new password for your account.</p>
        <label className="login-field">
          <span>New password</span>
          <input type="password" value={pw} autoComplete="new-password"
            onChange={(e) => setPw(e.target.value)} disabled={busy} autoFocus />
        </label>
        <label className="login-field">
          <span>Confirm password</span>
          <input type="password" value={pw2} autoComplete="new-password"
            onChange={(e) => setPw2(e.target.value)} disabled={busy} />
        </label>
        {error && <div className="login-error">{error}</div>}
        <button type="submit" className="login-submit" disabled={busy}>
          {busy ? "…" : "Reset password"}
        </button>
        <Link to="/login" className="login-toggle">Back to sign in</Link>
      </form>
    </main>
  );
}
```

- [ ] **Step 3: 在 `App.tsx` 加公开路由**

在未认证分支的 `<Routes>` 里（`boot === "unauthenticated"`）改为:
```tsx
  if (boot === "unauthenticated") {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/forgot" element={<ForgotPassword />} />
        <Route path="/reset" element={<ResetPassword />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }
```

并在文件顶部 import:
```tsx
import Login from "./pages/Login";
import ForgotPassword from "./pages/ForgotPassword";
import ResetPassword from "./pages/ResetPassword";
```

- [ ] **Step 4: typecheck**

```
cd frontend
npm run typecheck
```
Expected: 0 errors.

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/ForgotPassword.tsx frontend/src/pages/ResetPassword.tsx frontend/src/App.tsx
git commit -m "feat(ui): forgot + reset password pages; public routes"
```

---

## Task 9: SettingsView Account 区（设/改邮箱）

**Files:**
- Modify: `frontend/src/views/SettingsView.tsx`
- Modify: `frontend/src/store/settings.ts`（如 me/user 信息存在 settings store；否则从 `/api/auth/me` 取）

**Interfaces:**
- Consumes: `api.setAccountEmail`、当前用户邮箱（`getMe()` 或 settings store）。

- [ ] **Step 1: 确认当前用户邮箱来源**

读 `frontend/src/store/settings.ts`，确认是否已存 `email`。若 settings store 不含用户身份，则在 SettingsView 里 `getMe()` 拿当前邮箱初始化本地 state。下面的实现假设从 `getMe()` 取（最稳妥，不依赖 store 改造）。

- [ ] **Step 2: 在 SettingsView 顶部加 Account 区**

在 `SettingsView` 函数内加 state + effect + 渲染。先加 import:
```tsx
import * as api from "../lib/api";
```

在组件内（`location` 之后）加:
```tsx
const [email, setEmail] = useState<string | null>(null);
const [emailDraft, setEmailDraft] = useState("");
const [emailMsg, setEmailMsg] = useState<string | null>(null);
const [emailBusy, setEmailBusy] = useState(false);

useEffect(() => {
  api.getMe().then((me) => { if (me) { setEmail(me.email); setEmailDraft(me.email ?? ""); } });
}, []);

async function saveEmail() {
  setEmailBusy(true); setEmailMsg(null);
  try {
    const r = await api.setAccountEmail(emailDraft.trim() || null);
    setEmail(r.email); setEmailDraft(r.email ?? "");
    setEmailMsg("Saved.");
  } catch (e) {
    setEmailMsg((e as Error).message);
  } finally { setEmailBusy(false); }
}
```

在最外层 settings 容器最前面（第一个 `<section>`/heading 之前）加 Account 区:
```tsx
<section className="settings-section" id="account">
  <h2>Account</h2>
  <label className="login-field">
    <span>Username</span>
    <input type="text" value={username} disabled readOnly />
  </label>
  <label className="login-field">
    <span>Email (for password recovery)</span>
    <input type="email" value={emailDraft}
      onChange={(e) => setEmailDraft(e.target.value)} disabled={emailBusy} />
  </label>
  {emailMsg && <div className="login-error">{emailMsg}</div>}
  <button className="login-submit" onClick={saveEmail} disabled={emailBusy}>
    {emailBusy ? "…" : "Save email"}
  </button>
  {!email && <p className="login-hint">No email on file — add one to enable password recovery.</p>}
</section>
```

需要当前 `username`：若 SettingsView 未持有，从同一 `getMe()` 取 `me.username` 存入 state。补:
```tsx
const [username, setUsername] = useState("");
// in the same getMe().then:
//   setUsername(me.username);
```

- [ ] **Step 3: typecheck + 确认样式复用**

```
cd frontend
npm run typecheck
```
`.login-field` / `.login-submit` / `.login-hint` 已存在于 `index.css`，Account 区复用即可。若 `.settings-section` class 不存在，改用现有 settings 段落的 class（读 SettingsView 确认真实 class 名后再写）。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/views/SettingsView.tsx
git commit -m "feat(ui): Account section in Settings (set/change recovery email)"
```

---

## Task 10: 管理员 CLI `tools/reset_password.py`

**Files:**
- Create: `tools/reset_password.py`

**Interfaces:**
- Produces: 独立 CLI，`python tools/reset_password.py <username>` 直接改库密码哈希 + 可选清会话。

- [ ] **Step 1: 写脚本**

```python
"""Admin CLI: reset a user's password directly in the DB.

Escape hatch for the "I'm locked out right now" case — works even for accounts
with no email on file (which can't use the email reset flow). Does NOT need the
Fernet key: password hashing is bcrypt, independent of API-key encryption.

Usage (run from anywhere; resolves the DB path under backend/):
    python tools/reset_password.py <username>

You'll be prompted for the new password (not echoed). The script then:
  1. Looks up the user by username (case-insensitive).
  2. Updates password_hash to bcrypt(new_password).
  3. Optionally deletes all of that user's sessions (so any old cookie dies).
"""
from __future__ import annotations

import getpass
import os
import sqlite3
import sys
from pathlib import Path

import bcrypt


def _db_path() -> Path:
    backend = Path(__file__).resolve().parent.parent / "backend"
    url = os.environ.get("LAX_DATABASE_URL", "sqlite:///./little_alphaxiv.db")
    fname = url.split("///")[-1] if "///" in url else "little_alphaxiv.db"
    p = Path(fname)
    if not p.is_absolute():
        p = backend / p
    return p


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python tools/reset_password.py <username>", file=sys.stderr)
        return 2
    username = argv[1].strip().lower()
    db = _db_path()
    if not db.exists():
        print(f"[reset] DB not found at {db}. Start the backend once first.", file=sys.stderr)
        return 1
    con = sqlite3.connect(str(db))
    try:
        row = con.execute("SELECT id, username, email FROM user WHERE lower(username)=?", (username,)).fetchone()
        if row is None:
            print(f"[reset] no user named {username!r}", file=sys.stderr)
            return 1
        uid, uname, email = row
        print(f"[reset] user: {uname} (id={uid}, email={email or '<none>'})")
        pw = getpass.getpass("New password: ")
        if len(pw) < 8:
            print("[reset] password must be >= 8 chars", file=sys.stderr)
            return 1
        pw2 = getpass.getpass("Confirm: ")
        if pw != pw2:
            print("[reset] passwords do not match", file=sys.stderr)
            return 1
        hashed = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
        con.execute("UPDATE user SET password_hash=? WHERE id=?", (hashed, uid))
        n_sess = con.execute("SELECT count(*) FROM session WHERE user_id=?", (uid,)).fetchone()[0]
        if n_sess:
            ans = input(f"[reset] delete {n_sess} existing session(s) for this user? [y/N] ").strip().lower()
            if ans in ("y", "yes"):
                con.execute("DELETE FROM session WHERE user_id=?", (uid,))
                con.commit()
                print(f"[reset] cleared {n_sess} session(s); old cookies now invalid.")
            else:
                con.commit()
                print("[reset] sessions kept (old cookies remain valid until expiry).")
        else:
            con.commit()
        print(f"[reset] done. password updated for {uname}.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 2: 冒烟测试（无真实账号时验证路径解析 + 用户不存在分支）**

```
conda activate Agent_env
python tools/reset_password.py nonexistentuser </dev/null
```
Expected: `[reset] no user named 'nonexistentuser'`（退出码 1）。这验证 import + DB 路径 + 查询 OK。

- [ ] **Step 3: 提交**

```bash
git add tools/reset_password.py
git commit -m "feat(tools): admin CLI to reset a user's password directly"
```

---

## Task 11: Playwright E2E `tools/drive_password_reset.py`

**Files:**
- Create: `tools/drive_password_reset.py`

**Interfaces:**
- Consumes: 三服务（backend :8000 / frontend :5173 / mock LLM :5050 可选）+ 控制台邮件后端（`lax_reset_links.log`）。

- [ ] **Step 1: 写驱动**

```python
"""Playwright E2E: the email password-reset flow (console backend).

Covers the full link:
  1. Register with email → logged in.
  2. /forgot submit (by email) → backend writes reset link to lax_reset_links.log.
  3. Fresh context opens /reset?token=… → set new password → auto-login to /.
  4. Old password now fails to log in (401).
  5. Token is single-use: reusing it → 401.
  6. Forgot with unknown identifier still returns success (anti-enumeration).

Run with backend + frontend up. Scrapes the link from backend/lax_reset_links.log
(console mail backend). Defaults match the dev proxy.
"""
from __future__ import annotations

import codecs
import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")
sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, errors="replace")

FRONT = os.environ.get("LAX_FRONT", "http://127.0.0.1:5173")
BACK = os.environ.get("LAX_BACK", "http://127.0.0.1:8000")
LOG = Path(__file__).resolve().parent.parent / "backend" / "lax_reset_links.log"

USERNAME = f"e2e_{int(time.time()) % 100000}"
EMAIL = f"{USERNAME}@example.com"
PASSWORD = "oldpass123"
NEW_PASSWORD = "brandnew9"


def new_context(pw, headless=True):
    browser = pw.chromium.launch(headless=headless)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page._browser = browser  # type: ignore
    page._ctx = ctx  # type: ignore
    return page


def register_with_email(page):
    page.goto(f"{FRONT}/login", wait_until="domcontentloaded")
    page.wait_for_selector("input", timeout=10000)
    page.locator("text=Need an account? Register").click()
    page.locator("input[type=text]").fill(USERNAME)
    page.locator("input[type=email]").fill(EMAIL)
    page.locator("input[type=password]").fill(PASSWORD)
    page.locator("button.login-submit").click()
    page.wait_for_url(f"{FRONT}/", timeout=15000)
    print(f"REGISTER OK as {USERNAME}")


def latest_reset_link() -> str:
    text = LOG.read_text(encoding="utf-8")
    m = re.search(rf"{re.escape(EMAIL)}.*?(https?://\S+/reset\?token=\S+)", text)
    assert m, f"no reset link for {EMAIL} in {LOG}"
    return m.group(1)


def main():
    # Clean log so we don't grab a stale link.
    if LOG.exists():
        LOG.write_text("")
    with sync_playwright() as pw:
        page = new_context(pw, headless=True)
        try:
            register_with_email(page)
            # Logout back to /login so the fresh-context reset isn't pre-authed.
            page.request.post(f"{BACK}/api/auth/logout")

            # 2. Forgot via API (UI also works; API is deterministic).
            r = page.request.post(f"{BACK}/api/auth/forgot-password",
                                  data=f'{{"identifier":"{EMAIL}"}}',
                                  headers={"Content-Type": "application/json"})
            assert r.ok, f"forgot failed: {r.status}"
            link = latest_reset_link()
            token = link.split("token=")[1]
            print(f"GOT RESET LINK (token len={len(token)})")

            # 3. Fresh context opens /reset → set new password → land on /.
            page2 = new_context(pw, headless=True)
            page2.goto(f"{FRONT}/reset?token={token}", wait_until="domcontentloaded")
            page2.wait_for_selector("input[type=password]", timeout=10000)
            pw_inputs = page2.locator("input[type=password]")
            pw_inputs.nth(0).fill(NEW_PASSWORD)
            pw_inputs.nth(1).fill(NEW_PASSWORD)
            page2.locator("button.login-submit").click()
            page2.wait_for_url(f"{FRONT}/", timeout=15000)
            print("RESET+AUTOLOGIN OK")

            # 4. Old password fails.
            r4 = page2.request.post(f"{BACK}/api/auth/login",
                                    data=f'{{"username":"{USERNAME}","password":"{PASSWORD}"}}',
                                    headers={"Content-Type": "application/json"})
            assert r4.status == 401, f"old password should fail, got {r4.status}"
            # New password works.
            r5 = page2.request.post(f"{BACK}/api/auth/login",
                                    data=f'{{"username":"{USERNAME}","password":"{NEW_PASSWORD}"}}',
                                    headers={"Content-Type": "application/json"})
            assert r5.ok, f"new password should work, got {r5.status}"
            print("PASSWORD SWAP VERIFIED")

            # 5. Token single-use → reuse fails.
            r6 = page2.request.post(f"{BACK}/api/auth/reset-password",
                                    data=f'{{"token":"{token}","new_password":"yetanother1"}}',
                                    headers={"Content-Type": "application/json"})
            assert r6.status == 401, f"token reuse should fail, got {r6.status}"
            print("SINGLE-USE VERIFIED")

            # 6. Anti-enumeration: unknown identifier → 200.
            r7 = page2.request.post(f"{BACK}/api/auth/forgot-password",
                                    data='{"identifier":"definitely-nobody-xyz"}',
                                    headers={"Content-Type": "application/json"})
            assert r7.ok and r7.json()["ok"], "unknown identifier must return 200"
            print("ANTI-ENUMERATION VERIFIED")

            print("\nALL PASSWORD-RESET E2E CHECKS PASSED")
        finally:
            page._browser.close()  # type: ignore


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑全套 E2E（三服务 + 驱动）**

```
# 终端1: backend
cd backend && run.bat
# 终端2: frontend
cd frontend && npm run dev
# 终端3: 驱动（mock LLM 此流程不需要）
conda activate Agent_env
python tools/drive_password_reset.py
```
Expected: `ALL PASSWORD-RESET E2E CHECKS PASSED`。

- [ ] **Step 3: 提交**

```bash
git add tools/drive_password_reset.py
git commit -m "test(e2e): Playwright driver for the email password-reset flow"
```

---

## Task 12: 文档收尾 + 合并

**Files:**
- Modify: `README.md`（密码找回说明）
- Modify: `CLAUDE.md`（auth 段落补充 reset/account 端点 + 测试说明）

- [ ] **Step 1: README 加一节**

在 `README.md` 适合处加:
```markdown
## Forgot password

- On the login page, click **Forgot password?** and enter your username or
  email. A reset link is sent to the email on file.
- No SMTP configured? The link is printed to the backend terminal and appended
  to `backend/lax_reset_links.log` — fine for localhost.
- Configure SMTP via `LAX_SMTP_URL` (e.g. `smtps://user:pass@smtp.gmail.com:465`)
  for real email.
- Locked out with no email on file (pre-existing accounts)? Use the admin CLI:
  `python tools/reset_password.py <username>`
```

- [ ] **Step 2: CLAUDE.md auth 段落补充**

在 `CLAUDE.md` 的 auth 描述里补一句:
- `auth.py` 现在还有 `forgot-password` / `reset-password` / `PATCH /account`（设/改邮箱），`User.email` 可空唯一，`password_reset` 表存 sha256 哈希 token（单次+TTL）。重置成功即删该用户全部 Session。邮件 `email.py`（SMTP 或控制台）。`backend/tests/`（pytest，破例新增，安全敏感）。

- [ ] **Step 3: 跑全量后端测试 + 前端 typecheck/test 最后确认**

```
cd backend && python -m pytest tests/ -v
cd ../frontend && npm run typecheck && npm test
```
Expected: 全绿。

- [ ] **Step 4: 合并回 main（按项目 workflow）**

```
# 在 worktree 里
git add -A && git commit -m "docs: password recovery"   # 若有未提交
# 删除 frontend/node_modules junction 后退出 worktree，merge 进 main
```

按 CLAUDE.md "Working in worktrees" 流程：先 `rmdir` junction，再合并、删 worktree、push。

---

## Self-Review

**1. Spec coverage:**
- §1 流程 → Tasks 4,5,8,11 ✓
- §2 数据模型 + 迁移 → Task 1 ✓
- §3 forgot/reset/account 端点 + email.py + 枚举防护 → Tasks 2,4,5,6 ✓
- §4 前端 5 处 → Tasks 7,8,9 ✓
- §5 CLI → Task 10 ✓
- §6 env → Task 6 Step 5 ✓
- §7 测试（后端 pytest + Playwright）→ Tasks 3,4,5,6,11 ✓
- §8/9 安全要点 → 体现在 Tasks 1,2,4,5 ✓

**2. Placeholder scan:** Task 4 Step 1 有一个 `client._app_fixture_session()` 占位，但 Step 4 明确替换为 `dbmod.async_session_factory()` —— 这是 TDD 先红后绿的预期，非遗留 TODO。Task 9 Step 1/3 标注"读真实 class 名"——因 SettingsView 真实容器 class 未在计划上下文中确定，留作实现时按现有代码对齐（非占位，是必要的代码对齐步骤）。

**3. Type consistency:** `MeResponse` 在 Task 4 加 `email`，Task 5 reset 返回也用 `email=user.email` ✓；`register(username,email,password)` 在 Task 4 后端 + Task 7 前端签名一致 ✓；`resetPassword(token,newPassword)` Task 7 与 Task 11 调用一致 ✓；`send_reset_email(to,username,link)` Task 2 定义与 Task 4 调用一致 ✓；`PasswordResetRow` 字段名 Task 1 定义与 Task 4/5 查询一致 ✓。

计划完成。已保存到 `docs/superpowers/plans/2026-06-29-password-recovery.md`。
