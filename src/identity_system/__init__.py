"""identity-system - a central login service for sessionkit-backed apps.

Sits behind a reverse proxy's forward-auth hook (nginx auth_request, Traefik
ForwardAuth, Caddy forward_auth): GET /verify answers "who is this" for every
request to a protected app, and GET/POST /login is where a browser lands
when it isn't anyone yet. See README.md for the proxy wiring.
"""

from __future__ import annotations

__version__ = "0.1.0"
