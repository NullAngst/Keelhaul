"""Themes: one QSS template (base.qss) filled from per-theme color tokens.

Role rules that hold in every theme:
  accent  = go, on, healthy, success, focus
  danger  = caution, destructive, error. Same copper value in every theme,
            so a destructive control always looks the same.
Shape tokens (radii, padding, sizes) are shared across all themes.
"""

from __future__ import annotations

from pathlib import Path

DANGER = "#c26a3a"
DANGER_HOVER = "#d47b49"

SHAPE = {
    "radius": "6px",
    "radius_small": "4px",
    "tab_radius": "10px",
    "pad": "6px 10px",
    "font_size": "10pt",
    "mono": "'JetBrains Mono', 'DejaVu Sans Mono', 'Noto Sans Mono', monospace",
}

THEMES: dict[str, dict[str, str]] = {
    "copper": {
        "label": "Corroded Copper (dark)",
        "bg": "#0e181b", "surface": "#15242a", "surface2": "#1c3038", "border": "#2b4650",
        "text": "#e4ddd0", "muted": "#8ea29e", "input": "#0b1417",
        "accent": "#3fae96", "accent_hover": "#52c4ab", "accent_text": "#06110e",
        "tab": "#122026", "tab_text": "#a9b8b3", "select": "#24443f",
    },
    "dark": {
        "label": "Normal dark",
        "bg": "#1c1d20", "surface": "#26272b", "surface2": "#303136", "border": "#3d3f45",
        "text": "#e6e6e8", "muted": "#9a9ca3", "input": "#18191c",
        "accent": "#4f8ae6", "accent_hover": "#6a9df0", "accent_text": "#ffffff",
        "tab": "#212226", "tab_text": "#b4b6bc", "select": "#2f4466",
    },
    "light": {
        "label": "Normal light",
        "bg": "#eef0f3", "surface": "#ffffff", "surface2": "#f5f6f8", "border": "#c9ced6",
        "text": "#1d2026", "muted": "#5d6470", "input": "#ffffff",
        "accent": "#2563c9", "accent_hover": "#3473dc", "accent_text": "#ffffff",
        "tab": "#e2e5ea", "tab_text": "#4a515c", "select": "#cfdcf3",
    },
    "verdigris": {
        "label": "Verdigris",
        "bg": "#10302b", "surface": "#173c36", "surface2": "#1e4a42", "border": "#2f6358",
        "text": "#e6f2ee", "muted": "#9fc2b8", "input": "#0c2622",
        "accent": "#6fd1b5", "accent_hover": "#8adfc6", "accent_text": "#08211c",
        "tab": "#133631", "tab_text": "#b5d4cb", "select": "#2a5c52",
    },
    "kawaii_light": {
        "label": "Kawaii Light",
        "bg": "#fff3f8", "surface": "#ffffff", "surface2": "#fff8fb", "border": "#f2c3da",
        "text": "#4b2a3e", "muted": "#9a6b85", "input": "#ffffff",
        "accent": "#e85b9c", "accent_hover": "#f272ad", "accent_text": "#ffffff",
        "tab": "#fbe3ef", "tab_text": "#8a5574", "select": "#f8d2e4",
    },
    "kawaii_dark": {
        "label": "Kawaii Dark",
        "bg": "#221822", "surface": "#2d202e", "surface2": "#38283a", "border": "#56395a",
        "text": "#f7e4f0", "muted": "#c09ab3", "input": "#1b131c",
        "accent": "#ff86c2", "accent_hover": "#ff9fd0", "accent_text": "#2a0f1f",
        "tab": "#271c28", "tab_text": "#d8b7cb", "select": "#5a3555",
    },
}

DEFAULT = "copper"


def names() -> list[str]:
    return list(THEMES)


def stylesheet(name: str) -> str:
    tokens = dict(SHAPE)
    tokens.update(THEMES.get(name, THEMES[DEFAULT]))
    tokens["danger"] = DANGER
    tokens["danger_hover"] = DANGER_HOVER
    template = (Path(__file__).with_name("base.qss")).read_text(encoding="utf-8")
    for key, val in tokens.items():
        template = template.replace("@" + key + "@", val)
    return template


def tokens(name: str) -> dict[str, str]:
    t = dict(THEMES.get(name, THEMES[DEFAULT]))
    t["danger"] = DANGER
    return t
