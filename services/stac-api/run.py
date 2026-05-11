"""Startup script for stac-fastapi-pgstac.

Handles the app attribute name difference between stac-fastapi-pgstac 2.x (api)
and 3.x (app) so that the same entrypoint works after a version bump.
"""
import os
import sys
import uvicorn


def _load_app():
    mod = __import__("stac_fastapi.pgstac.app", fromlist=["app", "api"])
    asgi_app = getattr(mod, "app", None) or getattr(mod, "api", None)
    if asgi_app is None:
        print(
            "ERROR: cannot find ASGI app object in stac_fastapi.pgstac.app",
            file=sys.stderr,
        )
        sys.exit(1)
    return asgi_app


if __name__ == "__main__":
    host = os.environ.get("APP_HOST", "0.0.0.0")
    port = int(os.environ.get("APP_PORT", "8080"))
    root_path = os.environ.get("APP_ROOT_PATH", "")
    uvicorn.run(_load_app(), host=host, port=port, root_path=root_path)
