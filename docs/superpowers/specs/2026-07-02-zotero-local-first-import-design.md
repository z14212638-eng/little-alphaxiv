# Local-first Zotero PDF Import — Design

**Date:** 2026-07-02
**Status:** Approved (user sign-off 2026-07-02), implemented + deployed
**Worktree:** `zotero-local-first`
**Builds on:** `2026-07-01-local-paper-upload-design.md` (the Zotero reverse-import flow)

## 1. Background & goal

The Zotero reverse-import flow (`Open Paper → Import from Zotero`) downloads a
PDF from the user's Zotero library. The cloud path (`api.zotero.org` →
`zoterofilestorage.s3.us-east-1.amazonaws.com`) is flaky from a Docker
container whose egress bypasses the host proxy: the S3 host is intermittently
(sometimes persistently) interfered with at the TCP layer, hanging up to the OS
TCP timeout (~5 min) — the "stuck on Importing… forever" symptom. A prior fix
(retry + 30s per-attempt cap) bounds that to ~60s with a clear error, but
doesn't make the download *succeed*.

A second failure mode: the user's Zotero cloud storage quota is exhausted, so
some attachments never synced to zotero.org (`fileSize=0, linkMode=imported_file`,
`/file` returns 404 from the cloud). The cloud path can never fetch these.

**Goal:** read the PDF straight off the user's **local disk** (where it already
sits under the Zotero storage dir), falling back to the cloud download only
when local is unavailable. Local-first is fast, proxy-free, and immune to both
the S3 throttle and the cloud-quota gap (a file not synced to zotero.org is
still on local disk).

## 2. Key technical facts (verified 2026-07-02 against Zotero 7)

The local Zotero API (`127.0.0.1:23119`, read-only, no key) **does** expose file
download — but not as streamed bytes. `GET /api/users/0/items/<key>/file`
returns a **`302` redirect to a `file://` URL** pointing at the on-disk file:

```
Location: file:///C:/Users/Delig/Zotero/storage/BIA7MBBM/....pdf
```

This holds for **every** local attachment, including ones the cloud reports as
404 (the file is on disk even if not synced). So "local-first" means: ask the
local API for the path, then read the file off disk.

Two Docker constraints:
- The container can't reach `127.0.0.1:23119` (that's its own loopback). It
  reaches the host's Zotero via `host.docker.internal:23119`, but Zotero's
  local server rejects non-localhost `Host` headers (anti-CSRF, returns 400).
  Sending `Host: 127.0.0.1:23119` bypasses this (verified: 200).
- The container can't read `C:/Users/...`. The host storage dir is mounted
  read-only at `/zotero-storage`, and the `file://` host path is rewritten to
  that mount prefix.

## 3. Architecture

```
import_from_zotero (paper_uploads.py)
  └─ fetch_attachment_bytes(creds, att_key)        # NEW — the entry point
       ├─ try: download_local_attachment_bytes(att_key)   # NEW
       │     1. GET <LOCAL_BASE>/api/users/0/items/<key>/file   (Host: 127.0.0.1:23119)
       │     2. parse the 302 file:// Location
       │     3. _resolve_local_path: translate file:// -> readable path
       │        - native (no storage map): identity (read host path directly)
       │        - Docker (storage map set): rewrite host prefix -> /zotero-storage
       │        - reject paths outside the storage base (traversal guard)
       │     4. read bytes off disk (asyncio.to_thread), enforce _IMPORT_MAX_BYTES
       │     returns bytes, or raises _LocalUnavailable on ANY local failure
       └─ except _LocalUnavailable: download_attachment_bytes(creds, att_key)
            # existing cloud path, unchanged: retry + 30s per-attempt cap
```

- **`_LocalUnavailable`** is a sentinel exception — never surfaced to the user.
  The caller treats it purely as "fall back to cloud." Real errors (413
  oversize) still surface.
- **Local-first is always-on** (when local Zotero is reachable). It does not
  depend on the user's configured mode — even a Web-mode user gets local-first
  (the local API needs no creds). If local Zotero isn't running (native) or
  not mounted (Docker), the ping fails fast (2s) and falls back to cloud.
- **The cloud path is unchanged** — it's the fallback, with the retry + 30s cap
  already shipped.

## 4. Configuration (env vars, module-level in `zotero.py`)

| Env var | Default | Purpose |
|---|---|---|
| `LAX_ZOTERO_LOCAL_BASE` | `http://127.0.0.1:23119` | Local Zotero API base. Empty string `""` disables local-first (cloud only). Docker sets `http://host.docker.internal:23119`. |
| `LAX_ZOTERO_STORAGE_DIR` | `""` (empty) | Host-side Zotero storage path (Docker only). When set, `file://` paths under it are rewritten to `/zotero-storage/<rest>` (the mount). Empty = native, read host path directly. |

Docker `docker-compose.yml` adds both env vars + a read-only volume:
`${LAX_ZOTERO_STORAGE_DIR:-./data/zotero-storage-empty}:/zotero-storage:ro`.
Unset → an empty dir mounts (local reads fail → cloud fallback). Native
(`run.bat`) needs **no** config.

## 5. Error handling

- Every local failure (no local Zotero, connect error, non-302, file missing,
  path outside base, IO error) → `_LocalUnavailable` → silent cloud fallback.
- The user only ever sees an error when **both** paths fail — the existing
  cloud-error message (which already says "upload manually").
- Path-traversal guard: `_resolve_local_path` rejects any `file://` whose host
  prefix isn't the configured storage base, and any resolved path containing
  `..`. A crafted `att_key` can't read arbitrary host files via the local API.

## 6. Testing (10 new tests in `test_zotero_retry.py`)

- `_resolve_local_path`: native identity, Docker rewrite, rejects outside-base,
  rejects traversal.
- `download_local_attachment_bytes`: reads file from disk (tmp_path), disabled
  when base empty, unreachable → `_LocalUnavailable`, non-302 → `_LocalUnavailable`.
- `fetch_attachment_bytes`: local-unavailable → cloud fallback; local succeeds
  → cloud untouched.
- Existing `test_zotero_import.py` mocks updated to `fetch_attachment_bytes`.
- 76 pass total (was 66).

## 7. Out of scope

- Streaming local files larger than `_IMPORT_MAX_BYTES` (50 MiB) — surfaces a
  413, same as the cloud path.
- Auto-detecting the Zotero storage dir on first run (the `file://` redirect
  reveals it; a future nicety would be to auto-set `LAX_ZOTERO_STORAGE_DIR`
  from the first successful local read).
- The `LAX_ZOTERO_PROXY` cloud-proxy path (kept as an opt-in no-op; measured
  equally flaky for S3, superseded by local-first).
