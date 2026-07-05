"""Composition root: wires the MCP surface (tools.py) to the auth surface
(auth.py) and runs uvicorn.

The split is deliberate — tools.py is the high-churn side (Phase 3 keeps
adding tools), auth.py is the "do not regress" security baseline. Keep this
module to routing + startup so a diff here is always a wiring change.
"""

from __future__ import annotations

import os

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route

from . import auth, config, db
from .tools import mcp


async def healthz(_: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def build_app() -> Starlette:
    """Assemble the Starlette app: OAuth shim routes + bearer middleware
    around the FastMCP streamable-HTTP app mounted at /.

    Single-worker only: the rate limiter, OAuth token cache (auth.py), and
    pending auth codes (oauth.py) are in-process dicts. Running uvicorn with
    workers > 1 breaks the OAuth flow non-deterministically (auth code issued
    on one worker, consumed on another).
    """
    token = os.environ.get("MCP_AUTH_TOKEN")
    if not token:
        raise RuntimeError("MCP_AUTH_TOKEN env var is required")
    # Schema + migrations run once here, at startup — NOT on the per-request
    # read path (tools.py / bearer validation open plain connections). This
    # keeps DDL write locks off search_events and out of the nightly ingest's
    # way (issue #28).
    db.init_events(config.DB_PATH)
    db.init_oauth(config.OAUTH_DB_PATH)
    # streamable_http_app() lazily materializes mcp.session_manager. The inner
    # app's lifespan is what starts the session manager's task group; we have
    # to forward it through our outer Starlette or every request 500s with
    # "Task group is not initialized."
    mcp_app = mcp.streamable_http_app()
    return Starlette(
        routes=[
            Route("/healthz", healthz),
            Route(
                "/.well-known/oauth-protected-resource",
                auth.protected_resource_metadata,
            ),
            Route(
                "/.well-known/oauth-protected-resource/mcp",
                auth.protected_resource_metadata,
            ),
            Route(
                "/.well-known/oauth-authorization-server",
                auth.authorization_server_metadata,
            ),
            Route("/register", auth.register, methods=["GET", "POST"]),
            Route("/authorize", auth.authorize_get, methods=["GET"]),
            Route("/authorize", auth.authorize_post, methods=["POST"]),
            Route("/token", auth.token_endpoint, methods=["GET", "POST"]),
            Mount("/", app=mcp_app),
        ],
        middleware=[
            Middleware(
                auth.BearerAuthMiddleware,
                token=token,
                oauth_db_path=config.OAUTH_DB_PATH,
            ),
        ],
        lifespan=lambda _app: mcp.session_manager.run(),
    )


def main() -> None:
    app = build_app()
    # forwarded_allow_ips="*" lets request.base_url reflect the public
    # https://<host>/... that Tailscale Funnel forwards in X-Forwarded-* —
    # otherwise the OAuth discovery JSON would advertise http://0.0.0.0:8765/.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=config.PORT,
        proxy_headers=True,
        forwarded_allow_ips=config.FORWARDED_ALLOW_IPS,
    )


if __name__ == "__main__":
    main()
