"""Unit tests for `panakoes_notification.templates_loader`."""

from __future__ import annotations

from pathlib import Path

import pytest

from panakoes_notification.templates_loader import (
    TemplateNotFoundError,
    TemplateRenderer,
)


@pytest.mark.unit
def test_renderer_renders_welcome_template() -> None:
    """The shipped `welcome` template renders with vars."""
    renderer = TemplateRenderer()
    rendered = renderer.render("welcome", {"name": "Alice"})
    assert "Alice" in rendered.text
    assert rendered.html is None


@pytest.mark.unit
def test_renderer_renders_summary_ready_template() -> None:
    """The `summary_ready` template substitutes the transcript id."""
    renderer = TemplateRenderer()
    rendered = renderer.render(
        "summary_ready",
        {"transcript_id": "tr_42", "name": "Bob"},
    )
    assert "tr_42" in rendered.text
    assert "Bob" in rendered.text


@pytest.mark.unit
def test_renderer_raises_for_missing_template() -> None:
    """Asking for a template that does not exist raises the typed error."""
    renderer = TemplateRenderer()
    with pytest.raises(TemplateNotFoundError):
        renderer.render("does_not_exist", {})


@pytest.mark.unit
def test_renderer_renders_html_when_present(tmp_path: Path) -> None:
    """When both .txt.j2 and .html.j2 exist, both bodies are rendered."""
    (tmp_path / "alert.txt.j2").write_text("Hello {{ name }}")
    (tmp_path / "alert.html.j2").write_text("<p>Hello {{ name }}</p>")
    renderer = TemplateRenderer(template_dir=tmp_path)
    rendered = renderer.render("alert", {"name": "Carol"})
    assert rendered.text == "Hello Carol"
    assert rendered.html == "<p>Hello Carol</p>"


@pytest.mark.unit
def test_renderer_html_autoescapes_user_input(tmp_path: Path) -> None:
    """The HTML environment escapes user-provided variables."""
    (tmp_path / "alert.txt.j2").write_text("plain {{ name }}")
    (tmp_path / "alert.html.j2").write_text("<p>{{ name }}</p>")
    renderer = TemplateRenderer(template_dir=tmp_path)
    rendered = renderer.render("alert", {"name": "<script>x</script>"})
    # Plaintext: NOT escaped.
    assert "<script>" in rendered.text
    # HTML: escaped.
    assert rendered.html is not None
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html


@pytest.mark.unit
def test_renderer_template_dir_property() -> None:
    """`template_dir` exposes the configured directory."""
    renderer = TemplateRenderer()
    assert renderer.template_dir.name == "templates"
