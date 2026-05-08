"""Jinja2 template loading and rendering for notification bodies.

Templates live next to the service source under `templates/`. Each
notification template is a pair of files:

- `<name>.txt.j2`: plaintext body (autoescape OFF).
- `<name>.html.j2` (optional): HTML body (autoescape ON).

If a caller asks for a template that has no `.txt.j2`, the service
returns 404 to the API client. We deliberately raise a typed error
class here so the route layer can map it to the right HTTP code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateNotFound,
    select_autoescape,
)

# Co-locate templates with the package so they ship inside the wheel.
_DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates"


class TemplateNotFoundError(Exception):
    """Raised when neither a `.txt.j2` nor `.html.j2` exists for a name."""


class RenderedTemplate:
    """A rendered template body in plaintext and (optionally) HTML."""

    __slots__ = ("html", "text")

    def __init__(self, text: str, html: str | None = None) -> None:
        self.text = text
        self.html = html


class TemplateRenderer:
    """Loads and renders Jinja2 templates from the templates directory.

    Two `Environment` instances are kept side by side: one with
    autoescape OFF for the plaintext body, one with autoescape ON for
    HTML so user-supplied vars cannot inject markup. Both reuse the
    same loader so disk lookups stay local to the configured directory.
    """

    def __init__(self, template_dir: Path | None = None) -> None:
        directory = template_dir if template_dir is not None else _DEFAULT_TEMPLATE_DIR
        self._directory = directory
        loader = FileSystemLoader(str(directory))
        # The text environment is intentionally non-escaping: it renders
        # plaintext bodies for SES `Text`. The HTML environment below
        # uses `select_autoescape` and is what user-supplied HTML flows
        # through, so XSS risk lives there, not here.
        self._text_env = Environment(
            loader=loader,
            autoescape=False,  # noqa: S701  # plaintext body, escaping would corrupt it
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            auto_reload=False,
        )
        self._html_env = Environment(
            loader=loader,
            autoescape=select_autoescape(enabled_extensions=("html", "j2", "html.j2")),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            auto_reload=False,
        )

    @property
    def template_dir(self) -> Path:
        """The directory the renderer reads templates from."""
        return self._directory

    def render(self, name: str, variables: dict[str, Any]) -> RenderedTemplate:
        """Render the named template, returning text plus optional HTML.

        Raises `TemplateNotFoundError` if no `<name>.txt.j2` exists.
        """
        text_template_name = f"{name}.txt.j2"
        html_template_name = f"{name}.html.j2"
        try:
            text_template = self._text_env.get_template(text_template_name)
        except TemplateNotFound as exc:
            raise TemplateNotFoundError(name) from exc
        text_body = text_template.render(**variables)

        html_body: str | None = None
        try:
            html_template = self._html_env.get_template(html_template_name)
        except TemplateNotFound:
            html_template = None
        if html_template is not None:
            html_body = html_template.render(**variables)

        return RenderedTemplate(text=text_body, html=html_body)
