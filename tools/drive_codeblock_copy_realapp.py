"""Playwright REAL-APP test: code-block copy button + tooltip bubble.

Drives the running Docker app at http://127.0.0.1:8000 (backend+frontend
served same-origin by the container) against the mock LLM on :5050. Verifies
the copy button and its tooltip bubble land correctly on a REAL code block
rendered by the actual React components — not a static fixture.

Flow:
  1. Register a fresh user (UI) — httpOnly cookie set.
  2. Add the mock provider via API, base_url=http://host.docker.internal:5050/v1
     (the container's backend reaches the host mock through Docker's gateway).
  3. Send a chat. The mock's turn-1 emits a search tool call; turn-2 streams a
     markdown answer containing a ```python fenced block → the frontend renders
     a .code-block with a .code-block-copy button.
  4. Hover the copy button → assert the .tooltip-bubble appears BELOW the
     button and horizontally near it (not at the top-left corner of the page).
  5. Click the copy button → assert the "copied" check (✓) appears.

Prereqs: the Docker container `little-alphaxiv` running on :8000, and the
mock LLM bound to 0.0.0.0:5050 on the host (so the container can reach it via
host.docker.internal).

Usage:  python tools/drive_codeblock_copy_realapp.py
"""
from __future__ import annotations

import codecs
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")
sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, errors="replace")

APP = os.environ.get("LAX_FRONT", "http://127.0.0.1:8000")
BACK = os.environ.get("LAX_BACK", "http://127.0.0.1:8000")
# The container's backend reaches the host mock via Docker's host gateway.
MOCK_BASE = os.environ.get("MOCK_BASE", "http://host.docker.internal:5050/v1")

USERNAME = f"e2e_{int(time.time()) % 100000}"
PASSWORD = "testtest123"

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        failures.append(name)


def register_and_login(page):
    page.goto(f"{APP}/login", wait_until="domcontentloaded")
    page.wait_for_selector("input", timeout=10000)
    page.locator("text=Need an account? Register").click()
    page.locator("input[type=text]").fill(USERNAME)
    # Email is required at registration (added after the auth-persistence driver).
    page.locator("input[type=email]").fill(f"{USERNAME}@example.com")
    page.locator("input[type=password]").fill(PASSWORD)
    page.locator("button.login-submit").click()
    page.wait_for_url(f"{APP}/", timeout=15000)
    page.wait_for_selector(".app-main, .chat-empty, textarea", timeout=15000)
    print(f"REGISTER+LOGIN OK as {USERNAME}")


def add_provider_via_api(page):
    pid = f"mock-{USERNAME}"
    resp = page.request.post(
        f"{BACK}/api/providers",
        data=json.dumps({
            "id": pid, "name": "Mock", "base_url": MOCK_BASE,
            "api_key": "mock", "model": "mock-model", "is_default": True,
        }),
        headers={"Content-Type": "application/json"},
    )
    assert resp.ok or resp.status in (409, 500), f"add provider failed: {resp.status} {resp.text()}"
    print(f"PROVIDER ADDED (base_url={MOCK_BASE})")


def send_chat_until_codeblock(page):
    page.goto(APP, wait_until="domcontentloaded")
    page.wait_for_selector("textarea", timeout=15000)
    page.locator("textarea").first.fill("find me papers on vision transformers")
    page.locator(".composer-send-btn").click()
    # The mock emits a ```python block on turn 2 (after the tool call round-trip).
    page.wait_for_selector(".code-block", timeout=30000)
    page.wait_for_timeout(1500)  # let streaming + highlight settle
    print("CODE BLOCK RENDERED")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        # Grant clipboard so navigator.clipboard.writeText (the copy button's ✓
        # confirmation) works in headless Chromium — otherwise it rejects and the
        # ✓ never shows, which is a test-env artifact, not a product bug.
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=APP)
        page = ctx.new_page()
        try:
            register_and_login(page)
            add_provider_via_api(page)
            send_chat_until_codeblock(page)

            btn = page.locator(".code-block-copy").first
            cb = page.locator(".code-block").first
            btn.wait_for(state="visible", timeout=10000)

            # --- Button position: top-right of the code block, inside the box. ---
            btn_box = btn.bounding_box()
            cb_box = cb.bounding_box()
            right_gap = cb_box["x"] + cb_box["width"] - (btn_box["x"] + btn_box["width"])
            check(
                "button: top-RIGHT of code block (right gap ~6px)",
                abs(right_gap - 6) < 4,
                f"right_gap={right_gap:.1f}px btn_x={btn_box['x']:.1f} "
                f"cb_right={cb_box['x']+cb_box['width']:.1f}",
            )
            inside = (btn_box["x"] >= cb_box["x"]
                      and btn_box["x"] + btn_box["width"] <= cb_box["x"] + cb_box["width"]
                      and btn_box["y"] >= cb_box["y"]
                      and btn_box["y"] + btn_box["height"] <= cb_box["y"] + cb_box["height"])
            check("button: inside the code box (not outside)", inside,
                  f"btn=({btn_box['x']:.1f},{btn_box['y']:.1f},{btn_box['width']:.1f}x{btn_box['height']:.1f}) "
                  f"cb=({cb_box['x']:.1f},{cb_box['y']:.1f},{cb_box['width']:.1f}x{cb_box['height']:.1f})")

            # --- Hover the button → the tooltip bubble should open. ---
            btn.hover()
            try:
                page.wait_for_selector(".tooltip-bubble[data-show='true']", timeout=3000)
            except Exception:
                # The bubble may need a tiny paint nudge; move + re-hover.
                page.mouse.move(1, 1)
                page.wait_for_timeout(120)
                btn.hover()
                page.wait_for_selector(".tooltip-bubble[data-show='true']", timeout=3000)
            page.wait_for_timeout(150)  # 80ms fade + paint settle

            bub = page.locator(".tooltip-bubble[data-show='true']").first
            bub_box = bub.bounding_box()
            btn_box2 = btn.bounding_box()  # re-read in case of reflow
            assert bub_box and btn_box2, "missing bubble or button box"

            # Bubble should be BELOW the button (its top >= button bottom - 2px
            # tolerance) — NOT at the top-left of the page.
            below = bub_box["y"] >= btn_box2["y"] + btn_box2["height"] - 2
            # And horizontally near the button: the bubble's horizontal center
            # within ~12px of the button's horizontal center.
            bub_cx = bub_box["x"] + bub_box["width"] / 2
            btn_cx = btn_box2["x"] + btn_box2["width"] / 2
            near_x = abs(bub_cx - btn_cx) < 16
            check(
                "bubble: appears BELOW the button (not top-left)",
                below,
                f"bubble_y={bub_box['y']:.1f} btn_bottom={btn_box2['y']+btn_box2['height']:.1f}",
            )
            check(
                "bubble: horizontally near the button",
                near_x,
                f"bubble_cx={bub_cx:.1f} btn_cx={btn_cx:.1f} delta={abs(bub_cx-btn_cx):.1f}",
            )
            # Sanity: bubble text is "Copy".
            txt = bub.inner_text().strip()
            check("bubble: text is 'Copy'", txt == "Copy", f"got='{txt}'")

            page.screenshot(path=str(__import__("pathlib").Path(__file__).parent / "shots" / "codeblock_realapp.png"))
            print("SCREENSHOT: tools/shots/codeblock_realapp.png")

            # --- Click → copied check (✓) appears. ---
            page.mouse.move(1, 1)
            page.wait_for_timeout(150)  # hide bubble so it doesn't intercept the click
            btn.click()
            page.wait_for_timeout(200)
            txt2 = btn.inner_text().strip()
            check("click: button shows ✓ (copied)", txt2 == "✓", f"got='{txt2}'")
        finally:
            browser.close()

    n = len(failures)
    print(f"\n{'PASS' if n == 0 else 'FAIL'}: {n} check(s) failed"
          + ("" if not failures else " — " + ", ".join(failures)))
    sys.exit(1 if n else 0)


if __name__ == "__main__":
    main()
