"""Tests for scripts/serve_store.py."""

import json
import threading
import urllib.request
from collections.abc import Iterator

import pytest

from agent.config import load_settings
from scripts.serve_store import make_server, render_config


def test_render_config_exports_key_and_flag_name() -> None:
    settings = load_settings(
        _env_file=None,
        nvidia_api_key="nvapi-test",
        flagsmith_admin_api_key="admin-test",
        flagsmith_project_id=1,
        flagsmith_environment_key="env-key-123",
    )

    body = render_config(settings, "checkout_button").decode()

    assert body.startswith("export default ")
    config = json.loads(body.removeprefix("export default ").rstrip(";\n"))
    assert config == {"environmentID": "env-key-123", "flagName": "checkout_button"}


@pytest.fixture
def store_url() -> Iterator[str]:
    server = make_server(0, b"export default {};\n")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_serves_generated_config(store_url: str) -> None:
    with urllib.request.urlopen(f"{store_url}/config.js") as response:
        assert response.headers["Content-Type"].startswith("text/javascript")
        assert response.headers["Cache-Control"] == "no-store"
        assert response.read() == b"export default {};\n"


@pytest.mark.parametrize("path", ["/app.js", "/variant.js"])
def test_store_scripts_are_javascript(store_url: str, path: str) -> None:
    with urllib.request.urlopen(f"{store_url}{path}") as response:
        assert response.headers["Content-Type"].startswith("text/javascript")
