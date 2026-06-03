"""Unit tests for the HTML renderers.

These hit the rendering helpers directly so the defensive escaping
branches (which the catalog's static content never exercises) are
covered, and so a regression in the self-contained-page invariant
(no external asset references) fails loudly.
"""

from __future__ import annotations

from panakoes_api_index import catalog, render
from panakoes_api_index.render import _js_string


def test_landing_is_self_contained() -> None:
    html = render.landing_html()
    # No external stylesheets, scripts, fonts, or images: the only
    # network call is the same-origin health fetch in the inline script.
    assert "<link" not in html
    assert "src=" not in html
    assert "https://fonts" not in html
    # The inline script fetches the health probe path and nothing else.
    assert "fetch(" in html
    assert html.count("fetch(") == 1


def test_landing_has_no_em_dash() -> None:
    # Phil's house style bans em-dashes; the pre-push hook scans the whole
    # tree, so the dash is referenced by codepoint (U+2014) rather than as
    # a literal char to avoid tripping that hook on this very test.
    em_dash = chr(0x2014)
    assert em_dash not in render.landing_html()
    assert em_dash not in render.not_found_html("/x")


def test_not_found_escapes_path() -> None:
    html = render.not_found_html('/a"b<c>')
    assert '"b<c>' not in html
    assert "&lt;c&gt;" in html


def test_js_string_escapes_quotes_and_backslashes() -> None:
    assert _js_string("a'b") == "'a\\'b'"
    assert _js_string("a\\b") == "'a\\\\b'"
    assert _js_string("a\nb") == "'a\\nb'"


def test_js_string_round_trips_probe_path() -> None:
    # The real catalog path is quote-free, so the serialized literal is
    # just the path wrapped in single quotes.
    assert _js_string(catalog.HEALTH_PROBE_PATH) == f"'{catalog.HEALTH_PROBE_PATH}'"
