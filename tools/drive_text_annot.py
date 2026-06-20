"""Behavioral verification of the PDF Text-annotation fixes (bugs 1-4).

Runs against the worktree dev server (default http://127.0.0.1:5175) with a
real arXiv PDF. Checks:
  Bug 1 — Text tool then a single page click focuses the input immediately.
  Bug 2 — the committed text box hugs its content (width from text, not the
          old ~160px textarea default).
  Bug 3 — selecting the box then clicking blank PDF area clears the selection.
  Bug 4 — drag-selection across the text box does not swallow its text.

Usage:
  APP_URL=http://127.0.0.1:5175 PYTHONUTF8=1 PYTHONIOENCODING=utf-8 \
    /c/Users/Delig/.conda/envs/Agent_env/python.exe tools/_drive_text_annot.py
"""
from __future__ import annotations

import codecs
import os
import sys

from playwright.sync_api import sync_playwright

sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")
sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, errors="replace")

APP = os.environ.get("APP_URL", "http://127.0.0.1:5175").rstrip("/")
ARXIV_ID = "1706.03762"  # "Attention Is All You Need" — real arxiv fetch

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{APP}/paper/{ARXIV_ID}", wait_until="domcontentloaded")

        # Wait for the first PDF page canvas to render (real arxiv fetch).
        try:
            page.wait_for_selector("canvas", timeout=60_000)
            page.wait_for_function(
                "() => [...document.querySelectorAll('canvas')].some(c => c.width > 0)",
                timeout=60_000,
            )
            page.wait_for_selector(".annot-layer", timeout=30_000)
        except Exception as e:  # noqa: BLE001
            record("paper loads", False, f"canvas/annot-layer never ready: {e}")
            browser.close()
            return 1
        record("paper loads", True, "canvas + annot-layer present")

        # --- Bug 1: Text tool -> single page click -> input focused ---
        page.locator('button[title="Text"]').click()
        page.wait_for_selector('button[title="Text"].active', timeout=5_000)
        layer = page.locator(".annot-layer").first
        box = layer.bounding_box()
        assert box, "annot-layer has no box"
        page.mouse.click(box["x"] + 200, box["y"] + 200)
        try:
            page.wait_for_selector(".annot-text-input", timeout=5_000)
        except Exception:  # noqa: BLE001
            record("bug1 focus", False, "input never appeared after one click")
            browser.close()
            return 1
        focused = page.eval_on_selector(
            ".annot-text-input", "(el) => document.activeElement === el"
        )
        record("bug1 focus", focused, "input present & focused after a single click" if focused else "input not focused")

        # --- Bug 2: type, commit, box hugs content ---
        sample = "hello world"
        page.locator(".annot-text-input").click()
        page.locator(".annot-text-input").type(sample, delay=20)
        page.locator(".annot-text-input").press("Enter")
        try:
            page.wait_for_selector(".annot-text", timeout=5_000)
        except Exception:  # noqa: BLE001
            record("bug2 commit", False, "committed .annot-text never appeared")
            browser.close()
            return 1
        info = page.eval_on_selector(
            ".annot-text",
            "(el) => ({text: (el.textContent||'').trim(), w: el.getBoundingClientRect().width, h: el.getBoundingClientRect().height})",
        )
        record("bug2 commit text", info["text"] == sample, f"text={info['text']!r}")
        # Content-sized: "hello world" at ~14px is well under 140px; the old
        # textarea-default box was ~160px.
        record("bug2 box hugs content", 20 < info["w"] < 140,
               f"w={info['w']:.1f}px h={info['h']:.1f}px (old ~160px)")

        # --- Bug 3: select the box, then click blank -> deselect ---
        page.locator(".annot-text").click()
        try:
            page.wait_for_selector(".annot-text.selected", timeout=5_000)
            selected = True
        except Exception:  # noqa: BLE001
            selected = False
        record("bug3 select", selected, "box becomes selected on click")
        if selected:
            page.mouse.click(box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.6)
            try:
                page.wait_for_selector(".annot-text.selected", state="detached", timeout=5_000)
                deselected = True
            except Exception:  # noqa: BLE001
                deselected = False
            record("bug3 deselect", deselected,
                   "selection cleared on blank click" if deselected else "still selected after blank click")

        # --- Bug 4: drag-selection across the text box must not swallow its text ---
        ann = page.locator(".annot-text").first
        ab = ann.bounding_box()
        assert ab, "committed .annot-text has no box"
        mid_y = ab["y"] + ab["height"] / 2
        page.mouse.move(ab["x"] - 25, mid_y)
        page.mouse.down()
        page.mouse.move(ab["x"] + ab["width"] + 25, mid_y, steps=12)
        page.mouse.up()
        page.wait_for_timeout(200)
        sel = page.evaluate("() => window.getSelection().toString()")
        record("bug4 no selection bleed", sample not in sel,
               f"selection len={len(sel)}, contains annot text={sample in sel}")

        browser.close()

    print("\n=== summary ===")
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
