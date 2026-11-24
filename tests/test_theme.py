"""Guards for the light/dark theme switch.

The toggle is JavaScript, so its behaviour is exercised in a browser rather than
here. What these tests catch is the failure that actually shipped: `theme.css`
setting `data-theme="dark"` in markup while defining no dark token values, which
left the icon swapping and nothing else.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "app" / "static" / "css" / "theme.css").read_text(encoding="utf-8")

# Tokens whose value is deliberately the same in both themes: a dark-on-dark
# payload panel, ink printed on the gold pin, the white halo around pins, and
# shadows that fall onto the light map tiles.
THEME_INDEPENDENT = {
    "--payload-bg",
    "--payload-ink",
    "--pin-active-ink",
    "--map-halo",
    "--shadow-pin",
    "--shadow-pin-active",
    "--shadow-cluster",
}


def _block(selector: str) -> str:
    match = re.search(re.escape(selector) + r" \{(.*?)\n\}", CSS, re.S)
    assert match, f"theme.css has no {selector} block"
    return match.group(1)


def _colour_tokens(block: str) -> set[str]:
    names = re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", block)
    return {name for name, value in names if "#" in value or "rgb" in value}


def test_dark_theme_overrides_every_colour_token():
    missing = (
        _colour_tokens(_block(":root"))
        - THEME_INDEPENDENT
        - _colour_tokens(_block('[data-theme="dark"]'))
    )
    assert not missing, f"colour tokens with no dark value: {sorted(missing)}"


def test_dark_theme_sets_color_scheme():
    assert "color-scheme: dark;" in _block('[data-theme="dark"]')


def test_light_theme_sets_color_scheme():
    assert "color-scheme: light;" in _block(":root")


def test_toggle_button_carries_no_pressed_state():
    """The label already names the target mode, so aria-pressed would double up."""
    base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    toggle = re.search(r"<button[^>]*data-theme-toggle[^>]*>", base)
    assert toggle, "no data-theme-toggle button in base.html"
    assert "aria-pressed" not in toggle.group(0)
    assert "aria-label" in toggle.group(0)
