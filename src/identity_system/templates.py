"""Inline HTML templates: the login form and the admin user list. No JS
framework, no build step, no external stylesheet - these only need to be
usable, not pretty."""

from __future__ import annotations

from datetime import datetime
from html import escape

from sessionkit import User

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


_ADMIN_PAGE = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Users</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font: 16px system-ui, sans-serif; max-width: 48rem; margin: 3rem auto; padding: 0 1rem; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; }}
    th, td {{ text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #ddd; }}
    th {{ font-size: 0.85rem; text-transform: uppercase; color: #666; }}
  </style>
</head>
<body>
  <h1>Users</h1>
  <table>
    <thead>
      <tr><th>Email</th><th>Name</th><th>Created</th><th>Last login</th><th>2FA</th></tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</body>
</html>
"""

_ADMIN_ROW = """\
      <tr><td>{email}</td><td>{name}</td><td>{created_at}</td><td>{last_login_at}</td><td>{totp}</td></tr>\
"""


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else "never"


def render_admin_users(users: list[User]) -> str:
    rows = "\n".join(
        _ADMIN_ROW.format(
            email=escape(user.email),
            name=escape(user.name),
            created_at=_fmt(user.created_at),
            last_login_at=_fmt(user.last_login_at),
            totp="on" if user.totp_enabled else "off",
        )
        for user in users
    )
    return _ADMIN_PAGE.format(rows=rows)


__all__ = ["render_login", "render_admin_users"]
