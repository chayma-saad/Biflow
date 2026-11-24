"""Record the BIFlow LinkedIn demo with Playwright video (1920x1080).

Plays scenario 3 ("Écart corrigé par l'Auditeur", flag on Qualité / ETL) in the web app, then
shows the Auditor feedback loop, the orchestrator journal, the Qualité / ETL panel and the
published dashboard. Writes:

- <out>/raw/*.webm          the raw Playwright recording
- <out>/marks.json          seconds since the start of the video for each key moment
- <out>/shots/*.png         3 screenshots for the LinkedIn post

Usage: .venv/Scripts/python scripts/linkedin_demo/record.py [--url http://127.0.0.1:8000] [--out livrables/linkedin]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

W, H = 1920, 1080
ZOOM = 1.25  # larger UI so it stays readable in a LinkedIn feed on a phone

# Page zoom + visible cursor (Playwright videos do not show the mouse pointer).
# Under CSS zoom, mouse clientX/Y are screen pixels while `left/top` are zoomed CSS pixels.
CURSOR_JS = """
const ZOOM = %s;
window.addEventListener('DOMContentLoaded', () => {
  document.documentElement.style.zoom = String(ZOOM);
  const c = document.createElement('div');""" % ZOOM + """
  c.id = '__demo_cursor';
  Object.assign(c.style, {
    position: 'fixed', left: '-100px', top: '-100px', width: '22px', height: '22px',
    borderRadius: '50%', background: 'rgba(37, 99, 235, 0.35)', border: '2px solid #1d4ed8',
    zIndex: 2147483647, pointerEvents: 'none', transform: 'translate(-50%, -50%)',
    transition: 'width .15s, height .15s',
  });
  document.body.appendChild(c);
  document.addEventListener('mousemove', (e) => {
    c.style.left = e.clientX / ZOOM + 'px'; c.style.top = e.clientY / ZOOM + 'px';
  });
  document.addEventListener('mousedown', () => { c.style.width = c.style.height = '14px'; });
  document.addEventListener('mouseup', () => { c.style.width = c.style.height = '22px'; });
});
"""


class Recorder:
    def __init__(self, page: Page, t0: float, shots: Path) -> None:
        self.page, self.t0, self.shots = page, t0, shots
        self.marks: dict[str, float] = {}

    def mark(self, name: str) -> None:
        self.marks[name] = round(time.monotonic() - self.t0, 2)
        print(f"[{self.marks[name]:7.2f}s] {name}")

    def pause(self, seconds: float) -> None:
        self.page.wait_for_timeout(int(seconds * 1000))

    def click(self, locator, pause_after: float = 1.0) -> None:
        """Glide the cursor to the element, then click it."""
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        assert box, "element not visible"
        self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=30)
        self.pause(0.3)
        locator.click()
        self.pause(pause_after)

    def scroll_to(self, locator, offset: int = 80) -> None:
        """Smoothly scroll so the element sits near the top (below the sticky header)."""
        locator.evaluate(
            """(el, offset) => {
                el.style.scrollMarginTop = offset + 'px';
                el.scrollIntoView({ block: 'start', behavior: 'smooth' });
            }""",
            offset,
        )
        self.pause(1.4)

    def wheel(self, total: int, steps: int = 20, step_pause: float = 0.06) -> None:
        for _ in range(steps):
            self.page.mouse.wheel(0, total / steps)
            self.pause(step_pause)

    def shot(self, name: str) -> None:
        self.page.evaluate("document.getElementById('__demo_cursor')?.style.setProperty('display','none')")
        self.page.screenshot(path=str(self.shots / name))
        self.page.evaluate("document.getElementById('__demo_cursor')?.style.removeProperty('display')")
        print(f"          screenshot {name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default="livrables/linkedin")
    ap.add_argument("--run-timeout", type=float, default=900, help="seconds to wait for the run")
    args = ap.parse_args()

    out = Path(args.out)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    (out / "shots").mkdir(parents=True, exist_ok=True)
    run_ms = int(args.run_timeout * 1000)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": W, "height": H},
            record_video_dir=str(out / "raw"),
            record_video_size={"width": W, "height": H},
            locale="fr-FR",
        )
        ctx.add_init_script(CURSOR_JS)
        page = ctx.new_page()
        r = Recorder(page, time.monotonic(), out / "shots")  # video starts with the page

        # 1. Scenario page
        page.goto(f"{args.url}/#/demo")
        launch = page.get_by_role("button", name="Lancer le scénario")
        launch.wait_for()
        page.mouse.move(W / 2, H / 2)
        r.mark("form")
        r.pause(3)

        # 2. Scenario 3, flag on Qualité / ETL, slow mode on
        r.click(page.locator("label.scenario", has_text="Écart corrigé par l'Auditeur"), 1.5)
        r.mark("picked")
        step = page.get_by_label("Étape signalée par l'Auditeur")
        r.click(step, 0.3)
        step.select_option("quality_etl")
        page.keyboard.press("Escape")
        r.pause(1.5)
        slow = page.get_by_label("Ralentir pour la présentation", exact=False)
        if not slow.is_checked():
            r.click(slow, 0.5)

        # 3. Launch and follow the run
        r.mark("launch")
        r.click(launch, 0.5)
        page.locator("ol.journal-list li").first.wait_for(timeout=60_000)
        r.mark("running")
        page.locator("ol.journal-list li.j-success", has_text="Qualité / ETL").first.wait_for(timeout=run_ms)
        r.mark("etl_done")
        page.locator("ol.journal-list li.j-audit-ko").first.wait_for(timeout=run_ms)
        r.mark("audit_ko")
        page.locator("ol.journal-list li", has_text="2e passage").first.wait_for(timeout=run_ms)
        r.mark("rerun")
        page.locator("p.outcome").wait_for(timeout=run_ms)
        r.mark("done")
        r.pause(3)

        # 4. Auditor feedback loop on the line diagram
        r.scroll_to(page.locator("header.run-head"))
        r.mark("renvoi")
        r.shot("01_renvoi_auditeur.png")
        r.pause(5)

        # 5. Orchestrator journal
        r.scroll_to(page.locator("section.journal"))
        r.mark("journal")
        r.shot("02_journal_orchestrateur.png")
        r.pause(6)

        # 6. Qualité / ETL panel
        r.click(page.locator("nav.agent-tabs button", has_text="Qualité / ETL"), 0.5)
        r.scroll_to(page.locator("nav.agent-tabs"))
        r.mark("etl_panel")
        r.pause(4)
        r.wheel(500, steps=25)
        r.pause(4)

        # 7. Published dashboard
        r.click(page.locator("nav.nav a", has_text="Tableau de bord"), 0)
        page.locator("section.kpis").wait_for(timeout=60_000)
        page.evaluate("window.scrollTo(0, 0)")
        r.pause(1.5)
        r.mark("dashboard")
        r.shot("03_tableau_de_bord.png")
        r.pause(5)
        r.wheel(700, steps=35)
        r.pause(4)
        r.wheel(700, steps=35)
        r.pause(4)
        r.mark("end")

        video = page.video
        ctx.close()
        browser.close()
        raw = Path(video.path()) if video else None

    (out / "marks.json").write_text(
        json.dumps({"video": str(raw), "marks": r.marks}, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"video: {raw}")


if __name__ == "__main__":
    main()
