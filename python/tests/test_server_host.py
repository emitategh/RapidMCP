"""Choosing the interface the server listens on."""

import importlib.metadata

import rapidmcp
from rapidmcp import Client, RapidMCP
from rapidmcp.cli import _build_parser, cmd_run


async def test_server_bound_to_loopback_serves_loopback_clients():
    server = RapidMCP(name="hosted", version="0.1", host="127.0.0.1")

    async with server, Client(f"127.0.0.1:{server.port}") as client:
        assert await client.ping()


def test_cli_passes_host_to_run(tmp_path, monkeypatch):
    script = tmp_path / "srv.py"
    script.write_text("from rapidmcp import RapidMCP\nmcp = RapidMCP(name='x', version='1')\n")
    seen: dict = {}
    monkeypatch.setattr(RapidMCP, "run", lambda self, **kwargs: seen.update(kwargs))

    cmd_run(_build_parser().parse_args(["run", str(script), "--host", "127.0.0.1", "-p", "7000"]))

    assert seen == {"port": 7000, "host": "127.0.0.1"}


def test_reported_version_matches_the_installed_package():
    assert rapidmcp.__version__ == importlib.metadata.version("rapidmcp")
