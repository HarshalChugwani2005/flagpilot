"""Shared look and feel: CSS, status pills, friendly errors, variant previews and charts."""

import html
import re
from collections.abc import Mapping, Sequence
from typing import Any

import plotly.graph_objects as go
import streamlit as st

from agent.errors import FlagPilotError
from agent.guardrails import GuardrailCheck
from agent.models import VariantStats

# Reference data-viz palette: categorical slots 1-4 (fixed order), per theme.
SERIES = {
    "light": ["#257de0", "#f47230", "#00b894", "#f2a600"],
    "dark": ["#338ced", "#e76624", "#00a888", "#d28e05"],
}
NEUTRAL = {"light": "#b5b5b5", "dark": "#5c5c5c"}
STATUS = {"good": "#0aac23", "warning": "#fbb315", "critical": "#dc3c3d", "neutral": "#8a8a8a"}
INFO = "#257de0"

PILL_STYLE = {
    "Draft": ("neutral", "○"),
    "Running": ("info", "▶"),
    "Ready to decide": ("warning", "◆"),
    "Rolled out": ("good", "✓"),
}
GUARDRAIL_STYLE = {"pass": ("good", "✓"), "warn": ("warning", "!"), "fail": ("critical", "✕")}
VERDICT_STYLE = {
    "winner": ("good", "✓", "Winner found"),
    "not_significant": ("warning", "◆", "Not significant yet"),
    "not_enough_data": ("neutral", "○", "Not enough data yet"),
}

HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

CSS = """
<style>
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] {display: none;}
.block-container {padding-top: 2rem; max-width: 1200px;}
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]) {
  border-radius: 12px;
}
div[data-testid="stVerticalBlockBorderWrapper"] > div {padding: 4px;}
.fp-card {border: 1px solid rgba(128,128,128,.28); border-radius: 12px; padding: 20px;
  margin: 0 0 1rem 0;}
.fp-pill {display: inline-flex; align-items: center; gap: .4em; padding: .15em .75em;
  border-radius: 999px; font-size: .85rem; font-weight: 600; line-height: 1.6;
  border: 1px solid var(--fp-color);
  background: color-mix(in srgb, var(--fp-color) 14%, transparent);
  margin: 0 .35em .35em 0; white-space: nowrap;}
.fp-pill .fp-icon {color: var(--fp-color);}
.fp-banner {border-radius: 12px; padding: 16px 20px; margin-bottom: 1rem;
  border: 1px solid var(--fp-color);
  background: color-mix(in srgb, var(--fp-color) 12%, transparent);}
.fp-banner strong {font-size: 1.1rem;}
.fp-bar {display: flex; gap: 2px; height: 12px; border-radius: 6px; overflow: hidden;
  margin: .5rem 0;}
.fp-bar span {display: block; height: 100%;}
.fp-legend {font-size: .8rem; opacity: .85;}
.fp-legend i {display: inline-block; width: .7em; height: .7em; border-radius: 2px;
  margin: 0 .3em 0 .8em; vertical-align: baseline;}
.fp-wordmark {font-size: 1.6rem; font-weight: 800; letter-spacing: -.02em; margin: 0;}
.fp-dot {display: inline-block; width: .6em; height: .6em; border-radius: 50%; margin-right: .4em;}
</style>
"""


def inject_css() -> None:
    """Add FlagPilot's CSS to the page (call once per run, from the app shell)."""
    st.html(CSS)


def theme() -> str:
    """``"dark"`` or ``"light"``, following the viewer's active Streamlit theme."""
    return "dark" if getattr(st.context.theme, "type", None) == "dark" else "light"


def _color(role: str) -> str:
    """Colour for a status role."""
    return INFO if role == "info" else STATUS[role]


def pill(label: str, role: str, icon: str) -> str:
    """HTML for one status pill: icon + label, never colour alone."""
    return (
        f'<span class="fp-pill" style="--fp-color:{_color(role)}">'
        f'<span class="fp-icon">{icon}</span>{html.escape(label)}</span>'
    )


def status_pill(status: str) -> str:
    """Pill for an experiment status (Draft / Running / Ready to decide / Rolled out)."""
    role, icon = PILL_STYLE[status]
    return pill(status, role, icon)


def guardrail_pills(checks: Sequence[GuardrailCheck]) -> str:
    """One pill per guardrail rule, green / amber / red."""
    pills = []
    for check in checks:
        role, icon = GUARDRAIL_STYLE[check.status]
        pills.append(pill(f"{check.rule}: {check.message}", role, icon))
    return "".join(pills)


def verdict_banner(verdict: str, detail: str) -> None:
    """Coloured verdict banner for the results page."""
    role, icon, title = VERDICT_STYLE[verdict]
    st.html(
        f'<div class="fp-banner" style="--fp-color:{_color(role)}">'
        f"<strong>{icon} {title}</strong><br>{html.escape(detail)}</div>"
    )


def show_error(error: Exception) -> None:
    """Plain-language error with technical details tucked away; never a stack trace."""
    if isinstance(error, FlagPilotError):
        st.error(error.friendly)
        detail = error.detail
    else:
        st.error("Something went wrong. Please try again.")
        detail = f"{type(error).__name__}: {error}"
    if detail:
        with st.expander("Details"):
            st.code(detail, language=None)


def _readable_text(background: str) -> str:
    """Black or white, whichever reads better on ``background``."""
    value = background.lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    red, green, blue = (int(value[index : index + 2], 16) / 255 for index in (0, 2, 4))
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return "#111111" if luminance > 0.55 else "#ffffff"


def button_preview(value: Mapping[str, Any]) -> str | None:
    """HTML preview of a ``{"color", "label"}`` variant value, or None if it is not one."""
    color, label = value.get("color"), value.get("label")
    if not (isinstance(color, str) and HEX_COLOR.match(color) and isinstance(label, str)):
        return None
    return (
        f'<button type="button" style="background:{color};color:{_readable_text(color)};'
        "border:0;border-radius:10px;padding:12px 24px;font-size:1rem;font-weight:600;"
        f'cursor:default;width:100%">{html.escape(label)}</button>'
    )


def _palette(names: Sequence[str], neutral_name: str | None) -> dict[str, str]:
    """Fixed-order categorical colours by name; ``neutral_name`` gets the neutral grey."""
    mode = theme()
    colors = iter(SERIES[mode])
    return {name: NEUTRAL[mode] if name == neutral_name else next(colors) for name in names}


def split_bar_html(segments: Mapping[str, float], neutral_name: str | None = None) -> str:
    """Compact HTML stacked bar with a legend (for cards)."""
    colors = _palette(list(segments), neutral_name)
    bar = "".join(
        f'<span style="width:{share}%;background:{colors[name]}" '
        f'title="{html.escape(name)}: {share:g}%"></span>'
        for name, share in segments.items()
        if share > 0
    )
    legend = "".join(
        f'<i style="background:{colors[name]}"></i>{html.escape(name)} {share:g}%'
        for name, share in segments.items()
    )
    return f'<div class="fp-bar">{bar}</div><div class="fp-legend">{legend}</div>'


def split_chart(segments: Mapping[str, float], neutral_name: str | None = None) -> go.Figure:
    """Build a Plotly horizontal stacked bar of how visitors are split."""
    colors = _palette(list(segments), neutral_name)
    figure = go.Figure()
    for name, share in segments.items():
        figure.add_bar(
            y=["Visitors"],
            x=[share],
            name=name,
            orientation="h",
            marker={"color": colors[name], "line": {"width": 2, "color": "rgba(0,0,0,0)"}},
            text=[f"{name} {share:g}%" if share >= 12 else ""],
            textposition="inside",
            insidetextanchor="middle",
            hovertemplate=f"{html.escape(name)}: %{{x:g}}% of visitors<extra></extra>",
        )
    figure.update_layout(
        barmode="stack",
        height=150,
        margin={"l": 0, "r": 0, "t": 8, "b": 0},
        xaxis={"range": [0, 100], "ticksuffix": "%", "showgrid": False},
        yaxis={"visible": False},
        legend={"orientation": "h", "y": -0.35},
        bargap=0.25,
    )
    return figure


def conversion_chart(variants: Sequence[VariantStats], metric_name: str) -> go.Figure:
    """Bar per variant: conversion rate with 95% confidence-interval error bars."""
    color = SERIES[theme()][0]
    rates = [variant.rate * 100 for variant in variants]
    figure = go.Figure(
        go.Bar(
            x=[variant.name for variant in variants],
            y=rates,
            marker={"color": color, "cornerradius": 4},
            error_y={
                "type": "data",
                "symmetric": False,
                "array": [
                    variant.ci_high * 100 - rate
                    for variant, rate in zip(variants, rates, strict=True)
                ],
                "arrayminus": [
                    rate - variant.ci_low * 100
                    for variant, rate in zip(variants, rates, strict=True)
                ],
                "thickness": 2,
                "width": 8,
            },
            customdata=[
                [variant.ci_low * 100, variant.ci_high * 100, variant.conversions, variant.visitors]
                for variant in variants
            ],
            hovertemplate=(
                "<b>%{x}</b><br>%{y:.2f}% conversion<br>95% CI %{customdata[0]:.2f}%"
                "–%{customdata[1]:.2f}%<br>%{customdata[2]} of %{customdata[3]} visitors"
                "<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        title={"text": f"{metric_name}: conversion rate by variant (95% CI)", "font": {"size": 15}},
        height=360,
        margin={"l": 0, "r": 0, "t": 48, "b": 0},
        yaxis={"ticksuffix": "%", "rangemode": "tozero", "gridcolor": "rgba(128,128,128,.2)"},
        bargap=0.45,
        showlegend=False,
    )
    return figure
