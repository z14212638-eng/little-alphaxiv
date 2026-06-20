"""Reproduce the PDF text-selection highlight bug.

Flow under test (HighlightLayer.tsx):
  toggle highlight ON  ->  drag-select text in .pdf-textlayer  ->
  .highlight-bubble appears (6 swatches)  ->  click a swatch  ->
  expect a .highlight-rect to appear.

Symptom reported: bubble appears, clicking a color does NOT highlight text.

This script also diagnoses WHY: it asks document.elementFromPoint() what is
actually under the swatch's center. If the textlayer (z-index 2, pointer-events
auto, covers the whole page) sits ABOVE the highlight-layer (z-index 1) that
owns the bubble, the swatch's mousedown is pointer-blocked and pickColor never
runs.

Run (all three servers up: backend :8000, frontend :5173, mock_llm :5050):
    conda run -n Agent_env python tools/drive_highlight_repro.py
"""
import codecs, json, os, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")
APP = os.environ.get("APP_URL", "http://127.0.0.1:5173")
PROV = {"name": "mock", "base_url": "http://127.0.0.1:5050/v1", "api_key": "mock", "model": "mock-model"}
SHOTS = Path(__file__).parent / "shots_highlight"
SHOTS.mkdir(exist_ok=True)
ARXIV = "1706.03762"


def seed(page):
    page.goto(f"{APP}/settings", wait_until="networkidle")
    prov_json = json.dumps(PROV)
    page.evaluate(
        """(pj)=>{
          const p=JSON.parse(pj);
          localStorage.setItem('little-alphaxiv-settings',
            JSON.stringify({state:{providers:[Object.assign({id:'r'},p,{is_default:true})],
                             defaultProviderId:'r',theme:'dark'},version:0}));
        }""",
        prov_json,
    )
    page.evaluate("""async ()=>{
      const req=indexedDB.deleteDatabase('little-alphaxiv');
      await new Promise(r=>{req.onsuccess=r;req.onerror=r;req.onblocked=r;});
    }""")


with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    page = b.new_context(viewport={"width": 1500, "height": 950}).new_page()
    logs = []
    page.on("pageerror", lambda e: logs.append(str(e)))

    seed(page)
    page.goto(f"{APP}/paper/{ARXIV}", wait_until="domcontentloaded")
    # Wait for the first PDF page's text layer to populate (text selectable).
    page.wait_for_selector(".pdf-textlayer span", timeout=30000)
    # Give pdf.js a moment to finish sizing spans.
    page.wait_for_timeout(1500)
    print("STEP1_textlayer_spans:", page.eval_on_selector_all(".pdf-textlayer span", "els=>els.length"))

    # Pick two spans a few words apart on page 1 to drag-select between.
    span_info = page.evaluate(
        """()=>{const s=[...document.querySelectorAll('.pdf-page-wrap .pdf-textlayer span')];
        return s.slice(0,8).map(e=>{const r=e.getBoundingClientRect();return {text:e.textContent, x:r.left+r.width/2, y:r.top+r.height/2, w:r.width, h:r.height};});}"""
    )
    print("STEP2_first_spans:", json.dumps(span_info[:4]))
    if len(span_info) < 5:
        print("FAIL: not enough textlayer spans to select; aborting")
        b.close(); sys.exit(1)
    a = span_info[1]
    c = span_info[5]

    # Toggle highlight ON (🖍️ button).
    page.locator('button[title="Highlight (toggle)"]').click()
    page.wait_for_timeout(200)
    highlight_on = page.evaluate("()=>{/* read store via the toggle button active class */}")
    active = page.locator('button[title="Highlight (toggle)"]').get_attribute("class")
    print("STEP3_highlight_btn_class:", active)

    # Drag-select text from span a to span c (real mouse drag => selection + mouseup).
    page.mouse.move(a["x"], a["y"])
    page.mouse.down()
    page.mouse.move(c["x"], c["y"], steps=12)
    page.mouse.up()
    page.wait_for_timeout(400)

    bubble_count = page.eval_on_selector_all(".highlight-bubble", "els=>els.length")
    swatch_count = page.eval_on_selector_all(".highlight-bubble-swatch", "els=>els.length")
    print("STEP4_bubble_count:", bubble_count, "swatch_count:", swatch_count)
    page.screenshot(path=str(SHOTS / "01_bubble.png"))

    if swatch_count == 0:
        print("RESULT: no bubble appeared -> different bug path; pageerrors:", logs)
        b.close(); sys.exit(1)

    # Diagnose: what element is actually under the first swatch's center?
    diag = page.evaluate(
        """()=>{
          const sw=document.querySelector('.highlight-bubble-swatch');
          const r=sw.getBoundingClientRect();
          const cx=r.left+r.width/2, cy=r.top+r.height/2;
          const el=document.elementFromPoint(cx,cy);
          const host=sw.closest('.pdf-page-canvas-wrap')||sw.parentElement;
          const tl=document.querySelector('.pdf-textlayer');
          return {swatch_rect:{left:r.left,top:r.top,w:r.width,h:r.height},
                  element_under: el ? (el.tagName+'.'+(el.className||'')+' id='+(el.id||'')) : 'null',
                  is_swatch: el===sw,
                  host_tag: host ? host.className : 'null',
                  host_z: host ? getComputedStyle(host).zIndex : 'n/a',
                  textlayer_z: tl ? getComputedStyle(tl).zIndex : 'n/a',
                  textlayer_pe: tl ? getComputedStyle(tl).pointerEvents : 'n/a',
                  textlayer_covers_point: tl ? (()=>{const t=tl.getBoundingClientRect(); return cx>=t.left&&cx<=t.right&&cy>=t.top&&cy<=t.bottom;})() : false};
        }"""
    )
    print("STEP5_diag_under_swatch:", json.dumps(diag))

    # Count highlights before.
    before = page.eval_on_selector_all(".highlight-rect", "els=>els.length")
    print("STEP6_highlight_rects_before:", before)

    # Attempt a REAL click on the first swatch (what a user does).
    try:
        page.locator(".highlight-bubble-swatch").first.click(timeout=3000)
        print("STEP7_real_click: ok")
    except Exception as e:
        print("STEP7_real_click: FAILED ->", str(e)[:200])

    page.wait_for_timeout(400)
    after = page.eval_on_selector_all(".highlight-rect", "els=>els.length")
    print("STEP8_highlight_rects_after_real_click:", after)
    page.screenshot(path=str(SHOTS / "02_after_real_click.png"))

    # If real click failed to apply, directly dispatch mousedown on the swatch
    # to prove the apply logic itself works when the handler actually fires.
    if after == before:
        page.locator('button[title="Highlight (toggle)"]').click()  # ensure on
        page.wait_for_timeout(100)
        # re-select
        page.mouse.move(a["x"], a["y"]); page.mouse.down(); page.mouse.move(c["x"], c["y"], steps=12); page.mouse.up()
        page.wait_for_timeout(300)
        dispatched = page.evaluate(
            """()=>{
              const sw=document.querySelector('.highlight-bubble-swatch');
              if(!sw) return 'no-swatch';
              const ev=new MouseEvent('mousedown',{bubbles:true,cancelable:true,button:0});
              sw.dispatchEvent(ev);
              return 'dispatched';
            }"""
        )
        page.wait_for_timeout(400)
        after_dispatch = page.eval_on_selector_all(".highlight-rect", "els=>els.length")
        print("STEP9_dispatched_mousedown:", dispatched, "highlight_rects_after:", after_dispatch)
        page.screenshot(path=str(SHOTS / "03_after_dispatch.png"))

    print("PAGEERRORS:", logs)
    b.close()
