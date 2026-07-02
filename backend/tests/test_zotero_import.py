"""Zotero reverse-import endpoint tests.

Mocks the Zotero HTTP helpers (list/download/get-item) so no network is
needed; the real ``load_zotero_creds`` runs against the encrypted
``UserSettings.zotero_config`` (so the Fernet round-trip is exercised).
Covers: happy path with DOI-keyed metadata, per-user hash dedup, auto-pick
largest attachment, no-attachment 400, not-configured 400, and that the
imported paper serves bytes via /paper-upload/{id}.
"""
from __future__ import annotations

from urllib.parse import quote

from sqlmodel import select

from app import db as dbmod
from app.models import UserPaperUpload

PDF_BYTES = b"%PDF-1.4\nfake-zotero-pdf-bytes\n%%EOF"


async def _register(client, username="alice"):
    r = await client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "password123",
        },
    )
    assert r.status_code == 201, r.text


async def _mock_list(creds, item_key):
    return [
        {"key": "att1", "fileSize": 100, "contentType": "application/pdf"},
        {"key": "att2", "fileSize": 200, "contentType": "application/pdf"},
    ], "web"


async def _mock_list_empty(creds, item_key):
    return [], "web"


async def _mock_download(creds, att_key):
    return PDF_BYTES


async def _mock_get_item(creds, item_key):
    return {
        "title": "Zotero Paper",
        "creators": "Alice; Bob",
        "abstract": "ab",
        "doi": "10.1/x",
        "url": "https://example.com/p",
    }


async def _configure_zotero(client):
    r = await client.patch(
        "/api/settings",
        json={"zotero": {"mode": "web", "userId": "u1", "apiKey": "k1"}},
    )
    assert r.is_success, r.text


async def test_import_from_zotero_happy_path(client, tmp_path, monkeypatch):
    monkeypatch.setenv("LAX_PDF_CACHE", str(tmp_path / "pdf_cache"))
    monkeypatch.setattr("app.routers.paper_uploads.list_pdf_attachments", _mock_list)
    monkeypatch.setattr("app.routers.paper_uploads.fetch_attachment_bytes", _mock_download)
    monkeypatch.setattr("app.routers.paper_uploads.get_zotero_item", _mock_get_item)
    await _register(client)
    await _configure_zotero(client)
    r = await client.post(
        "/api/paper-upload/import-from-zotero", json={"item_key": "it1"}
    )
    assert r.is_success, r.text
    body = r.json()
    assert body["paper_id"] == "doi:10.1/x"
    assert body["source"] == "zotero"
    assert body["title"] == "Zotero Paper"
    assert body["authors"] == ["Alice", "Bob"]
    assert body["is_new"] is True
    # The upload row records the Zotero provenance + auto-picked largest PDF.
    async with dbmod.async_session_factory() as s:
        up = (
            await s.exec(
                select(UserPaperUpload).where(UserPaperUpload.paper_id == "doi:10.1/x")
            )
        ).first()
    assert up.zotero_item_key == "it1"
    assert up.zotero_attachment_key == "att2"  # largest (200 > 100)
    assert up.source == "zotero"
    # Imported bytes serve via the standard upload endpoint. DOI ids contain
    # '/', so the serve route uses a :path converter — the raw id can go in
    # the URL verbatim (the frontend paperUploadUrl does the same).
    sr = await client.get("/api/paper-upload/doi:10.1/x")
    assert sr.status_code == 200
    assert sr.content == PDF_BYTES


async def test_import_dedup_by_hash(client, tmp_path, monkeypatch):
    monkeypatch.setenv("LAX_PDF_CACHE", str(tmp_path / "pdf_cache"))
    monkeypatch.setattr("app.routers.paper_uploads.list_pdf_attachments", _mock_list)
    monkeypatch.setattr("app.routers.paper_uploads.fetch_attachment_bytes", _mock_download)
    monkeypatch.setattr("app.routers.paper_uploads.get_zotero_item", _mock_get_item)
    await _register(client)
    await _configure_zotero(client)
    r1 = await client.post(
        "/api/paper-upload/import-from-zotero", json={"item_key": "it1"}
    )
    r2 = await client.post(
        "/api/paper-upload/import-from-zotero", json={"item_key": "it1"}
    )
    assert r1.json()["paper_id"] == r2.json()["paper_id"]
    assert r2.json()["is_new"] is False


async def test_import_no_attachment_returns_400(client, tmp_path, monkeypatch):
    monkeypatch.setenv("LAX_PDF_CACHE", str(tmp_path / "pdf_cache"))
    monkeypatch.setattr("app.routers.paper_uploads.list_pdf_attachments", _mock_list_empty)
    monkeypatch.setattr("app.routers.paper_uploads.fetch_attachment_bytes", _mock_download)
    monkeypatch.setattr("app.routers.paper_uploads.get_zotero_item", _mock_get_item)
    await _register(client)
    await _configure_zotero(client)
    r = await client.post(
        "/api/paper-upload/import-from-zotero", json={"item_key": "it1"}
    )
    assert r.status_code == 400
    assert "no PDF attachment" in r.text


async def test_import_zotero_not_configured(client, tmp_path, monkeypatch):
    monkeypatch.setenv("LAX_PDF_CACHE", str(tmp_path / "pdf_cache"))
    await _register(client)
    r = await client.post(
        "/api/paper-upload/import-from-zotero", json={"item_key": "it1"}
    )
    assert r.status_code == 400
    assert "not configured" in r.text


async def test_import_explicit_attachment_key(client, tmp_path, monkeypatch):
    monkeypatch.setenv("LAX_PDF_CACHE", str(tmp_path / "pdf_cache"))
    monkeypatch.setattr("app.routers.paper_uploads.list_pdf_attachments", _mock_list)
    monkeypatch.setattr("app.routers.paper_uploads.fetch_attachment_bytes", _mock_download)
    monkeypatch.setattr("app.routers.paper_uploads.get_zotero_item", _mock_get_item)
    await _register(client)
    await _configure_zotero(client)
    # Caller pins the attachment explicitly → no list call needed.
    r = await client.post(
        "/api/paper-upload/import-from-zotero",
        json={"item_key": "it1", "attachment_key": "att-pinned"},
    )
    assert r.is_success, r.text
    async with dbmod.async_session_factory() as s:
        up = (
            await s.exec(
                select(UserPaperUpload).where(UserPaperUpload.paper_id == "doi:10.1/x")
            )
        ).first()
    assert up.zotero_attachment_key == "att-pinned"


# --------------------------------------------------------------------------- #
# Local-first status endpoint: tells the UI whether local-disk PDF import is
# usable, with an actionable hint when it isn't. Drives the Settings → Zotero
# status line + the Import-dialog hint so Docker users who skipped the env vars
# see WHY imports fall back to the slow cloud path.
# --------------------------------------------------------------------------- #
async def _lf(client, monkeypatch, *, local_base, alive, storage_host="", mount_dir=None):
    """Call /api/zotero/local-first-status with the module config mocked."""
    from app.routers import zotero as zmod
    monkeypatch.setattr(zmod, "_LOCAL_BASE", local_base)
    monkeypatch.setattr(zmod, "_STORAGE_MAP_HOST", storage_host)
    if mount_dir is not None:
        monkeypatch.setattr(zmod, "_STORAGE_CONTAINER_PREFIX", str(mount_dir).replace("\\", "/"))
    async def _fake_alive():
        return alive
    monkeypatch.setattr(zmod, "_local_first_alive", _fake_alive)
    await _register(client, "lfuser")
    r = await client.get("/api/zotero/local-first-status")
    assert r.status_code == 200, r.text
    return r.json()


async def test_local_first_status_disabled(client, monkeypatch):
    # LAX_ZOTERO_LOCAL_BASE empty -> local-first off, hint explains.
    body = await _lf(client, monkeypatch, local_base=None, alive=False)
    assert body["available"] is False
    assert body["configured"] is False
    assert body["hint"]


async def test_local_first_status_native_active(client, monkeypatch, tmp_path):
    # Native (default base) + Zotero running -> fully active, no hint.
    body = await _lf(client, monkeypatch, local_base="http://127.0.0.1:23119", alive=True)
    assert body["available"] is True
    assert body["configured"] is True
    assert body["hint"] is None


async def test_local_first_status_native_zotero_off(client, monkeypatch):
    # Native + Zotero not running -> available False, hint says start Zotero.
    body = await _lf(client, monkeypatch, local_base="http://127.0.0.1:23119", alive=False)
    assert body["available"] is False
    assert body["configured"] is False
    assert "Zotero" in body["hint"]


async def test_local_first_status_docker_active(client, monkeypatch, tmp_path):
    # Docker (overridden base) + storage mounted -> active.
    body = await _lf(client, monkeypatch, local_base="http://host.docker.internal:23119",
                     alive=True, storage_host="C:/Zotero/storage", mount_dir=tmp_path)
    assert body["available"] is True
    assert body["configured"] is True
    assert body["hint"] is None


async def test_local_first_status_docker_no_mount(client, monkeypatch, tmp_path):
    # Docker + Zotero reachable but storage dir not mounted -> configured False,
    # hint names LAX_ZOTERO_STORAGE_DIR.
    body = await _lf(client, monkeypatch, local_base="http://host.docker.internal:23119",
                     alive=True, storage_host="", mount_dir=tmp_path)
    assert body["available"] is True
    assert body["configured"] is False
    assert "LAX_ZOTERO_STORAGE_DIR" in body["hint"]


async def test_local_first_status_docker_unreachable(client, monkeypatch):
    # Docker + can't reach host Zotero -> available False, hint mentions Docker env.
    body = await _lf(client, monkeypatch, local_base="http://host.docker.internal:23119", alive=False)
    assert body["available"] is False
    assert body["configured"] is False
    assert "LAX_ZOTERO_LOCAL_BASE" in body["hint"]
