"""Cut the Playwright recording into the LinkedIn video (MP4 H.264, 1920x1080, <= 90 s).

- title card, then the recording with the waiting parts sped up (a "xN" badge says so),
  then the end card;
- short French subtitles burned in (the video is watched without sound);
- figures in the subtitles come from the run itself (data/processed/<run_id>/run.json).

Usage: .venv/Scripts/python scripts/linkedin_demo/edit.py [--out livrables/linkedin]
Needs ffmpeg (libx264 + libass) on PATH, and record.py run first.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

W, H, FPS = 1920, 1080, 30
MAX_SECONDS = 90
TITLE_S, END_S = 4.0, 5.0

TITLE = "BIFlow — Automatiser la BI avec des agents IA"
TITLE_SUB = "Pipeline BI multi-agents appliqué au service public · demandes NYC 311"
END_LINES = ["Chaima Saad — Architecture, Orchestrator et agent Qualité/ETL",
             "Projet d'équipe ESPRIT — Groupe 2"]

CARD_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;width:1920px;height:1080px;background:#0b1a3a;color:#fff;
  font-family:'Segoe UI',system-ui,sans-serif;overflow:hidden}
.wrap{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;
  text-align:center;padding:0 160px;box-sizing:border-box;
  background:radial-gradient(1200px 600px at 50% 40%,#1e3a8a 0%,#0b1a3a 70%)}
.logo{display:flex;gap:14px;align-items:center;margin-bottom:46px}
.logo i{width:34px;height:34px;border-radius:50%;background:#60a5fa;display:block}
.logo i.o{background:transparent;border:7px solid #60a5fa;box-sizing:border-box}
.logo b{width:44px;height:7px;background:#60a5fa;display:block;border-radius:4px}
h1{font-size:84px;font-weight:700;line-height:1.15;margin:0 0 34px;letter-spacing:-1px}
p{font-size:40px;line-height:1.45;margin:0;color:#cbd5e1}
p.strong{color:#fff;font-weight:600;font-size:48px;margin-bottom:18px}
.tag{margin-top:60px;font-size:28px;color:#93c5fd;letter-spacing:2px;text-transform:uppercase}
</style></head><body><div class="wrap">
<div class="logo"><i></i><b></b><i class="o"></i><b></b><i></i></div>
{BODY}
</div></body></html>"""


@dataclass
class Seg:
    start: float
    end: float
    target: float | None = None  # output length; None = real time

    @property
    def speed(self) -> float:
        if self.target is None:
            return 1.0
        return max(1.0, (self.end - self.start) / self.target)

    @property
    def out_len(self) -> float:
        return (self.end - self.start) / self.speed


def ff(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True, cwd=cwd)


def run_figures(run_id: str | None, processed: Path) -> dict[str, str]:
    runs = sorted(processed.glob("*/run.json"), key=lambda p: p.stat().st_mtime)
    path = processed / run_id / "run.json" if run_id else runs[-1]
    run = json.loads(path.read_text(encoding="utf-8"))
    etl = run["outputs"]["quality_etl"]
    audit = run["outputs"]["auditor_xai"]
    checks = audit.get("checks", [])
    passed = sum(1 for c in checks if c.get("passed"))

    def fr(n: int) -> str:
        return f"{n:,}".replace(",", " ")  # espace fine insécable

    return {
        "run_id": run["run_id"], "status": run["status"], "published": str(run.get("published")),
        "rows_in": fr(etl["rows_in"]), "rows_out": fr(etl["rows_out"]),
        "quarantine": fr(etl["quarantine_rows"]), "rules": str(len(etl.get("rules_applied", []))),
        "passed": str(passed), "total": str(len(checks)),
    }


def ass_time(t: float) -> str:
    cs = round(t * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def write_ass(path: Path, lines: list[tuple[float, float, str]], badges: list[tuple[float, float, str]]) -> None:
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Segoe UI Semibold,50,&H00FFFFFF,&H00FFFFFF,&H28140B00,&H28140B00,0,0,0,0,100,100,0,0,3,22,0,2,180,180,70,1
Style: Badge,Segoe UI,36,&H00FFFFFF,&H00FFFFFF,&H50EB6325,&H50EB6325,1,0,0,0,100,100,0,0,3,12,0,9,0,130,205,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = [f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Sub,,0,0,0,,{{\\fad(200,200)}}{t}" for a, b, t in lines]
    ev += [f"Dialogue: 1,{ass_time(a)},{ass_time(b)},Badge,,0,0,0,,{t}" for a, b, t in badges]
    path.write_text(head + "\n".join(ev) + "\n", encoding="utf-8-sig")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="livrables/linkedin")
    ap.add_argument("--processed", default="data/processed")
    ap.add_argument("--run-id", default=None, help="run filmed (default: most recent run.json)")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)
    rec = json.loads((out / "marks.json").read_text(encoding="utf-8"))
    raw, m = Path(rec["video"]).resolve(), rec["marks"]
    fig = run_figures(args.run_id, Path(args.processed))
    print("run:", fig)

    # 1. Title and end cards, rendered as HTML so the typography is clean
    title = f"<h1>{TITLE}</h1><p>{TITLE_SUB}</p>"
    end = (f"<p class='strong'>{END_LINES[0]}</p><p>{END_LINES[1]}</p>"
           "<div class='tag'>BIFlow · Pipeline BI multi-agents</div>")
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": W, "height": H})
        for name, body in (("title", title), ("end", end)):
            page.set_content(CARD_HTML.replace("{BODY}", body))
            page.wait_for_timeout(300)
            page.screenshot(path=str(work / f"{name}.png"))
        b.close()

    # 2. Segments of the recording (seconds in the raw video)
    a = m["audit_ko"]
    segs = [
        Seg(m["form"] - 0.3, m["launch"] + 1.2),            # choice of scenario, real time
        Seg(m["launch"] + 1.2, m["etl_done"] + 0.8, 6.0),   # Profiler, Qualité/ETL
        Seg(m["etl_done"] + 0.8, a - 0.6, 6.0),             # KPI, Analyste, Dashboard, Auditeur
        Seg(a - 0.6, a + 2.4),                              # the Auditor flags the gap, real time
        Seg(a + 2.4, m["done"] + 1.0, 4.0),                 # rerun from Qualité/ETL, second audit
        Seg(m["done"] + 1.0, m["end"]),                     # walkthrough, real time
    ]

    def to_out(t: float) -> float:
        """Raw-video time -> time in the final video."""
        acc = TITLE_S
        for s in segs:
            if t <= s.end:
                return acc + max(0.0, t - s.start) / s.speed
            acc += s.out_len
        return acc

    files = []
    for i, s in enumerate(segs):
        f = work / f"seg{i}.mp4"
        ff("-ss", f"{s.start:.3f}", "-to", f"{s.end:.3f}", "-i", str(raw), "-an",
           "-vf", f"setpts=(PTS-STARTPTS)/{s.speed:.4f},fps={FPS},format=yuv420p",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", str(f))
        files.append(f)
        print(f"segment {i}: {s.end - s.start:6.1f}s -> {s.out_len:5.1f}s (x{s.speed:.1f})")
    for name, dur in (("title", TITLE_S), ("end", END_S)):
        ff("-loop", "1", "-t", str(dur), "-i", str(work / f"{name}.png"),
           "-vf", f"fps={FPS},format=yuv420p,fade=t=in:st=0:d=0.5,fade=t=out:st={dur - 0.5}:d=0.5",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", str(work / f"{name}.mp4"))
    order = [work / "title.mp4", *files, work / "end.mp4"]
    (work / "concat.txt").write_text("".join(f"file '{f.as_posix()}'\n" for f in order), encoding="utf-8")
    ff("-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy", "joined.mp4", cwd=work)

    total = TITLE_S + sum(s.out_len for s in segs) + END_S
    t = to_out
    subs = [
        (TITLE_S + 0.3, t(m["picked"]) - 0.1,
         "Des données brutes du service public (NYC 311) au tableau de bord, sans intervention manuelle."),
        (t(m["picked"]), t(m["launch"] + 1.2),
         "Scénario de test : on injecte un écart dans l'étape Qualité/ETL."),
        (t(m["launch"] + 1.2), t(m["etl_done"] + 0.8),
         "L'Orchestrator (LangGraph) coordonne 6 agents spécialisés, étape par étape."),
        (t(m["etl_done"] + 0.8), t(a - 0.6),
         "Les indicateurs sont calculés en SQL : aucun chiffre ne vient du LLM."),
        (t(a - 0.6), t(m["renvoi"]),
         "L'Auditeur détecte l'écart : l'Orchestrator renvoie vers Qualité/ETL et relance la suite."),
        (t(m["renvoi"]), t(m["journal"]),
         "Le renvoi est tracé sur la ligne : l'étape Qualité/ETL est rejouée, puis tout l'aval."),
        (t(m["journal"]), t(m["etl_panel"]) - 1.5,
         f"Second audit : {fig['passed']}/{fig['total']} contrôles réussis, le run est publié."),
        (t(m["etl_panel"]) - 1.5, t(m["dashboard"]) - 1.5,
         f"Agent Qualité/ETL : {fig['rules']} règles tracées, {fig['quarantine']} lignes en quarantaine, "
         f"{fig['rows_out']} conservées."),
        (t(m["dashboard"]) - 1.5, total - END_S,
         "Tableau de bord final : 5 KPI, tendance, anomalies, et le SQL derrière chaque chiffre."),
    ]
    badges = [(t(s.start), t(s.end), f"▶▶ ×{round(s.speed)}") for s in segs if s.speed > 1.5]
    write_ass(work / "subs.ass", subs, badges)

    # 3. Burn subtitles, encode H.264 for LinkedIn (silent AAC track: some players expect audio)
    final = out / "BIFlow_LinkedIn.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", "joined.mp4",
         "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
         "-vf", "ass=subs.ass", "-map", "0:v", "-map", "1:a", "-shortest",
         "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-profile:v", "high", "-pix_fmt", "yuv420p",
         "-r", str(FPS), "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(final)],
        check=True, cwd=work,
    )
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(final)], capture_output=True, text=True, check=True).stdout.strip()
    dur = float(probe)
    print(f"final: {final} ({dur:.1f}s)")
    if dur > MAX_SECONDS:
        raise SystemExit(f"video too long: {dur:.1f}s > {MAX_SECONDS}s")


if __name__ == "__main__":
    main()
