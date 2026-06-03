"""HTML rendering for the api-index landing and 404 pages.

The pages are fully self-contained: inline CSS, no external stylesheets,
no web fonts, no analytics. The only network call the landing page makes
is a same-origin fetch to the health probe path to render a live status
badge. That keeps the page fast, private, and safe to show in an
interview without leaking traffic to third parties.

All dynamic values that reach the HTML are escaped via `html.escape`.
The catalog content is static and author-controlled, but the requested
path on the 404 page is attacker-controlled, so it is always escaped.
"""

from __future__ import annotations

from html import escape

from . import catalog

_BASE_CSS: str = """
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: #0b0e14;
    color: #e6e9ef;
    font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }
  .wrap { max-width: 860px; margin: 0 auto; padding: 48px 24px 72px; }
  h1 { font-size: 2.2rem; margin: 0 0 4px; letter-spacing: -0.01em; }
  .tagline { color: #9aa4b2; margin: 0 0 28px; font-size: 1.05rem; }
  .badge {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 6px 12px; border-radius: 999px; font-size: 0.85rem;
    background: #161b26; border: 1px solid #232a39; color: #9aa4b2;
  }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: #6b7280; }
  .dot.ok { background: #2ecc71; }
  .dot.down { background: #e74c3c; }
  table { width: 100%; border-collapse: collapse; margin: 28px 0 8px; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #1c2230; vertical-align: top; }
  th { color: #9aa4b2; font-weight: 600; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; }
  td.route { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: #8ab4f8; white-space: nowrap; }
  td.desc { color: #c7cdd9; }
  .links { margin: 28px 0 0; display: flex; gap: 16px; flex-wrap: wrap; }
  .links a {
    color: #8ab4f8; text-decoration: none; padding: 8px 14px;
    border: 1px solid #232a39; border-radius: 8px; background: #11151f;
  }
  .links a:hover { border-color: #2e80ff; }
  footer { margin-top: 40px; color: #6b7280; font-size: 0.82rem; }
  code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: #c7cdd9; }
"""


def _rows() -> str:
    return "\n".join(
        f'      <tr><td class="route">{escape(route)}</td>'
        f"<td class=\"desc\">{escape(desc)}</td></tr>"
        for route, desc in catalog.ENDPOINTS
    )


def landing_html() -> str:
    """Return the full HTML landing page for browser clients."""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="robots" content="noindex" />
  <title>Panakoes API</title>
  <style>{_BASE_CSS}</style>
</head>
<body>
  <main class="wrap">
    <h1>Panakoes</h1>
    <p class="tagline">{escape(catalog.DESCRIPTION)}.</p>
    <span class="badge"><span id="status-dot" class="dot"></span><span id="status-text">checking auth service...</span></span>

    <table>
      <thead><tr><th>Route</th><th>Description</th></tr></thead>
      <tbody>
{_rows()}
      </tbody>
    </table>

    <div class="links">
      <a href="{escape(catalog.SOURCE_URL)}">Source (GitHub)</a>
      <a href="{escape(catalog.DASHBOARD_URL)}">Admin dashboard</a>
      <a href="{escape(catalog.HEALTH_PROBE_PATH)}">Auth health (JSON)</a>
    </div>

    <footer>
      Public HTTP API for Panakoes, a LaFayette Labs LLC project. All routes are versioned under <code>/v1</code>.
      Request <code>GET /</code> with <code>Accept: application/json</code> for the machine-readable index.
    </footer>
  </main>

  <script>
    (function () {{
      var dot = document.getElementById("status-dot");
      var text = document.getElementById("status-text");
      fetch({_js_string(catalog.HEALTH_PROBE_PATH)}, {{ headers: {{ "Accept": "application/json" }} }})
        .then(function (r) {{ return r.ok ? r.json() : Promise.reject(r.status); }})
        .then(function (body) {{
          dot.className = "dot ok";
          text.textContent = "auth service: " + (body.status || "ok");
        }})
        .catch(function () {{
          dot.className = "dot down";
          text.textContent = "auth service: unreachable";
        }});
    }})();
  </script>
</body>
</html>
"""


def not_found_html(path: str) -> str:
    """Return a small HTML 404 page for browser clients."""
    safe_path = escape(path)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="robots" content="noindex" />
  <title>Not found - Panakoes API</title>
  <style>{_BASE_CSS}</style>
</head>
<body>
  <main class="wrap">
    <h1>404</h1>
    <p class="tagline">No route matched <code>{safe_path}</code>.</p>
    <div class="links">
      <a href="/">Route index</a>
      <a href="{escape(catalog.SOURCE_URL)}">Source (GitHub)</a>
    </div>
  </main>
</body>
</html>
"""


def _js_string(value: str) -> str:
    """Serialize a Python string as a safe single-quoted JS string literal.

    The catalog path is author-controlled, but serialize defensively so a
    future path with a quote or backslash cannot break out of the literal.
    """
    escaped = value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return f"'{escaped}'"
