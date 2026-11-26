"""Serves demo-store/ locally and generates /config.js from .env (the key never lives in a file)."""

import argparse
import json
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.audit import AuditLog  # noqa: E402
from agent.config import Settings, load_settings  # noqa: E402
from agent.errors import FlagPilotError  # noqa: E402
from agent.experiments import demo_flag_name  # noqa: E402

STORE_DIR = ROOT / "demo-store"


def render_config(settings: Settings, flag_name: str) -> bytes:
    """Return the body of ``config.js``: an ES module exporting the store's Flagsmith settings."""
    config = {
        "environmentID": settings.flagsmith_environment_key.get_secret_value(),
        "flagName": flag_name,
    }
    return f"export default {json.dumps(config)};\n".encode()


class StoreHandler(SimpleHTTPRequestHandler):
    """Static file handler with a generated ``/config.js`` and correct JS MIME types.

    Windows can map ``.js`` to ``text/plain`` in the registry, which browsers refuse
    for ES modules, so the type is set explicitly.
    """

    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
    }

    def __init__(self, *args: object, config_js: bytes, **kwargs: object) -> None:
        """Keep the generated config before the base class handles the request."""
        self.config_js = config_js
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def do_GET(self) -> None:
        """Serve ``/config.js`` from memory and everything else from ``demo-store/``."""
        if self.path.split("?", 1)[0] != "/config.js":
            super().do_GET()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(self.config_js)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(self.config_js)


def make_server(port: int, config_js: bytes) -> ThreadingHTTPServer:
    """Create (but do not start) the store server on ``localhost:port``."""
    handler = partial(StoreHandler, directory=str(STORE_DIR), config_js=config_js)
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main() -> None:
    """Parse arguments, load settings and serve until Ctrl+C."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    try:
        settings = load_settings()
        entries = AuditLog(settings.audit_log_path).entries()
        flag_name = demo_flag_name(settings.store_flag_name, entries)
        config_js = render_config(settings, flag_name)
    except FlagPilotError as error:
        sys.exit(error.friendly)
    try:
        server = make_server(args.port, config_js)
    except OSError as error:
        sys.exit(f"Could not start the demo store on port {args.port}: {error.strerror}")
    print(
        f"Demo store running at http://localhost:{args.port} for flag {flag_name} (Ctrl+C to stop)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Demo store stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
