"""One inline HTML template for the login form. No JS framework, no build
step, no external stylesheet - this only needs to be usable, not pretty."""

from __future__ import annotations

from html import escape

_PAGE = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Sign in</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font: 16px system-ui, sans-serif; max-width: 22rem; margin: 4rem auto; padding: 0 1rem; }}
    label {{ display: block; margin-top: 0.75rem; font-size: 0.9rem; }}
    input {{ width: 100%; padding: 0.5rem; font-size: 1rem; box-sizing: border-box; }}
    button {{ margin-top: 1.25rem; width: 100%; padding: 0.6rem; font-size: 1rem; }}
    .error {{ color: #b00020; margin-top: 1rem; }}
  </style>
</head>
<body>
  <h1>Sign in</h1>
  {error_html}
  <form method="post" action="/login">
    <input type="hidden" name="rd" value="{rd}">
    <label for="email">Email</label>
    <input id="email" name="email" type="email" value="{email}" required autofocus>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" required>
    <label for="otp">2FA code (only if you have it enabled)</label>
    <input id="otp" name="otp" type="text" inputmode="numeric" autocomplete="one-time-code">
    <button type="submit">Sign in</button>
  </form>
</body>
</html>
"""


def render_login(*, rd: str = "", email: str = "", error: str | None = None) -> str:
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    return _PAGE.format(rd=escape(rd), email=escape(email), error_html=error_html)


__all__ = ["render_login"]
