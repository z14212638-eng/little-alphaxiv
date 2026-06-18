"""Test math rendering: inject a conversation with LaTeX, check KaTeX output."""
import codecs, sys, json
sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")
from playwright.sync_api import sync_playwright

APP = "http://127.0.0.1:5173"
PROV = {"name":"ppio-glm","base_url":"https://api.ppio.com/openai/v1","api_key":"sk_FwuagHFdkC_UxM75YOZkZ6Aetu-9JulmUpmnHYgz6A0","model":"zai-org/glm-5.2"}

# The conversation to inject (plain JSON, no JS escaping issues)
CONV = {
    "id": "m1",
    "title": "Math Test",
    "type": "general",
    "messages": [
        {"role": "user", "content": "explain softmax"},
        {"role": "assistant", "content": "The softmax function:\n\n$$\\text{softmax}(x_i) = \\frac{e^{x_i}}{\\sum_j e^{x_j}}$$\n\nInline math: $E = mc^2$"}
    ],
    "created_at": 1,
    "updated_at": 1,
}

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    page = b.new_context(viewport={"width": 1500, "height": 800}).new_page()
    page.goto(f"{APP}/settings", wait_until="networkidle")
    page.evaluate(
        """(pj) => {const p=JSON.parse(pj);localStorage.setItem("little-alphaxiv-settings",JSON.stringify({state:{providers:[Object.assign({id:"r"},p,{is_default:true})],defaultProviderId:"r",sidebarCollapsed:false},version:0}))}""",
        json.dumps(PROV),
    )
    page.goto(APP, wait_until="networkidle")
    # Inject conversation via IDB
    conv_json = json.dumps(CONV)
    page.evaluate(
        """(cj) => new Promise(r => {
            const conv = JSON.parse(cj);
            const req = indexedDB.open("little-alphaxiv", 1);
            req.onsuccess = () => {
                const db = req.result;
                const tx = db.transaction("conversations", "readwrite");
                tx.objectStore("conversations").put(conv);
                tx.oncomplete = () => r();
            };
        })""",
        conv_json,
    )
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1500)
    # Click the "Math Test" conversation
    page.evaluate("""() => {
        const el = [...document.querySelectorAll(".conv-item")].find(e => /Math/.test(e.textContent));
        if (el) el.click();
    }""")
    page.wait_for_timeout(1500)
    k = page.evaluate("() => ({katex: document.querySelectorAll('.katex').length, display: document.querySelectorAll('.katex-display').length})")
    print("MATH_RENDER:", json.dumps(k))
    page.screenshot(path=str(__import__("pathlib").Path(__file__).parent / "shots_real" / "math_test.png"))
    b.close()
