import re
from pathlib import Path

CSS_DIR = Path(__file__).resolve().parents[1] / "static" / "css"
VARS = (CSS_DIR / "variables.css").read_text()

REQUIRED = [
    "--radius-xs", "--radius-sm", "--radius-md", "--radius-lg", "--radius-xl", "--radius-pill",
    "--space-1", "--space-2", "--space-3", "--space-4", "--space-5", "--space-6",
    "--shadow-1", "--shadow-2", "--shadow-3",
    "--surface-1", "--surface-2", "--surface-3",
    "--dur-fast", "--dur", "--dur-slow", "--ease-out", "--ease-in-out",
]


def test_tokens_defined_on_root():
    for name in REQUIRED:
        assert re.search(rf"^\s*{re.escape(name)}\s*:", VARS, re.M), name


def test_single_value_radius_literals_migrated():
    # After migration, single-value 4/6/8/12/999px radii must use tokens.
    pat = re.compile(r"border-radius:\s*(4|6|8|12|999)px\s*;")
    hits = []
    for f in CSS_DIR.glob("*.css"):
        if f.name == "variables.css":
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if pat.search(line):
                hits.append(f"{f.name}:{i}")
    assert len(hits) < 20, hits[:40]


def test_base_font_fallback_is_inter():
    base = (CSS_DIR / "base.css").read_text()
    assert re.search(r"html\s*\{[^}]*font-family:\s*var\(--font-family,\s*'Inter'", base)


def test_surface_tokens_used_by_composer_and_popovers():
    allcss = "\n".join(f.read_text() for f in CSS_DIR.glob("*.css"))
    assert allcss.count("var(--surface-3)") >= 3
    assert allcss.count("var(--surface-2)") >= 2


def test_global_reduced_motion_guard():
    base = (CSS_DIR / "base.css").read_text()
    assert "@media (prefers-reduced-motion: reduce)" in base
    assert "animation-duration: .01ms !important" in base or "animation-duration: 0.01ms !important" in base


def test_motion_tokens_adopted():
    # Flags ANY raw duration inside a transition declaration (not just the
    # values we've historically mapped) so a newly-introduced literal can't
    # slip back in unnoticed. 0s/0ms are allowed since there's no token for
    # "instant" and no visual difference to migrate.
    raw_duration = re.compile(r"(?<![\w-])\d*\.?\d+m?s\b")
    for name in ("layout-chat.css", "layout-sidebar.css", "overlays.css", "chat-components.css"):
        css = (CSS_DIR / name).read_text()
        assert "var(--dur" in css and "var(--ease-out)" in css, name

        # Scan every transition declaration, including ones that sit mid-line
        # inside one-liner rule blocks (common in overlays.css).
        decl = re.compile(r"transition(?:-duration)?\s*:[^;{}]*")
        offenders = []
        for i, line in enumerate(css.splitlines(), 1):
            for m in decl.finditer(line):
                tokens = raw_duration.finditer(m.group(0))
                if any(tok.group(0) not in ("0s", "0ms") for tok in tokens):
                    offenders.append(f"{name}:{i}")
        assert not offenders, offenders
