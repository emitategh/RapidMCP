"""Tests for the FasterMCP CLI helper functions.

We test the pure-Python helpers (parse_file_path, import_server) without
spawning a subprocess — the actual `server.run()` path is covered by the
existing gRPC integration tests.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from mcp_grpc.cli import _CANDIDATE_NAMES, import_server, parse_file_path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_server(tmp_path: Path):
    """Write a temporary server file and return its path."""

    def _make(content: str, filename: str = "server.py") -> Path:
        p = tmp_path / filename
        p.write_text(textwrap.dedent(content))
        return p

    return _make


# ---------------------------------------------------------------------------
# parse_file_path
# ---------------------------------------------------------------------------


def test_parse_file_path_no_object(tmp_path: Path):
    f = tmp_path / "server.py"
    f.touch()
    path, obj = parse_file_path(str(f))
    assert path == f.resolve()
    assert obj is None


def test_parse_file_path_with_object(tmp_path: Path):
    f = tmp_path / "server.py"
    f.touch()
    path, obj = parse_file_path(f"{f}:my_app")
    assert path == f.resolve()
    assert obj == "my_app"


def test_parse_file_path_missing_file_exits(tmp_path: Path):
    with pytest.raises(SystemExit) as exc:
        parse_file_path(str(tmp_path / "nonexistent.py"))
    assert exc.value.code == 1


def test_parse_file_path_directory_exits(tmp_path: Path):
    with pytest.raises(SystemExit) as exc:
        parse_file_path(str(tmp_path))
    assert exc.value.code == 1


def test_parse_file_path_colon_in_object(tmp_path: Path):
    """Object names with colons are not expected, but rsplit handles gracefully."""
    f = tmp_path / "server.py"
    f.touch()
    # Only the *last* colon is treated as separator
    path, obj = parse_file_path(f"{f}:my_app")
    assert obj == "my_app"


# ---------------------------------------------------------------------------
# import_server — auto-discovery
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("var_name", list(_CANDIDATE_NAMES))
def test_import_server_auto_discovers_candidate(tmp_server, var_name: str):
    """Auto-discovery finds each of the candidate variable names."""
    f = tmp_server(
        f"""
        from mcp_grpc import FasterMCP
        {var_name} = FasterMCP("{var_name}-server", "1.0")
        """
    )
    from mcp_grpc import FasterMCP

    server = import_server(f)
    assert isinstance(server, FasterMCP)
    assert server.name == f"{var_name}-server"


def test_import_server_prefers_first_candidate(tmp_server):
    """When multiple candidate names exist, the first one wins."""
    f = tmp_server(
        """
        from mcp_grpc import FasterMCP
        mcp = FasterMCP("first", "1.0")
        server = FasterMCP("second", "1.0")
        app = FasterMCP("third", "1.0")
        """
    )
    server = import_server(f)
    assert server.name == "first"


def test_import_server_explicit_object(tmp_server):
    """Explicit object name is used directly."""
    f = tmp_server(
        """
        from mcp_grpc import FasterMCP
        my_custom = FasterMCP("custom", "1.0")
        """
    )
    from mcp_grpc import FasterMCP

    server = import_server(f, server_object="my_custom")
    assert isinstance(server, FasterMCP)
    assert server.name == "custom"


def test_import_server_explicit_object_missing_exits(tmp_server):
    """Explicit object name that does not exist → SystemExit(1)."""
    f = tmp_server(
        """
        from mcp_grpc import FasterMCP
        app = FasterMCP("app", "1.0")
        """
    )
    with pytest.raises(SystemExit) as exc:
        import_server(f, server_object="nonexistent")
    assert exc.value.code == 1


def test_import_server_no_candidates_exits(tmp_server):
    """No recognised variable name → SystemExit(1)."""
    f = tmp_server(
        """
        from mcp_grpc import FasterMCP
        my_server = FasterMCP("obscure", "1.0")
        """
    )
    with pytest.raises(SystemExit) as exc:
        import_server(f)
    assert exc.value.code == 1


def test_import_server_syntax_error_exits(tmp_server):
    """Module with a syntax error → SystemExit(1)."""
    f = tmp_server("this is not valid python !!!!")
    with pytest.raises(SystemExit) as exc:
        import_server(f)
    assert exc.value.code == 1


def test_import_server_adds_parent_to_sys_path(tmp_server):
    """import_server inserts the file's directory into sys.path."""
    f = tmp_server(
        """
        from mcp_grpc import FasterMCP
        app = FasterMCP("path-test", "1.0")
        """
    )
    parent = str(f.parent)
    if parent in sys.path:
        sys.path.remove(parent)

    import_server(f)
    assert parent in sys.path


# ---------------------------------------------------------------------------
# CLI argument parsing (no server needed)
# ---------------------------------------------------------------------------


def test_cli_run_requires_server_spec():
    """``fastermcp run`` without FILE exits with usage error."""
    from mcp_grpc.cli import _build_parser

    parser = _build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["run"])
    assert exc.value.code != 0


def test_cli_run_default_port():
    """``fastermcp run file.py`` sets default port 50051."""
    from mcp_grpc.cli import _build_parser

    parser = _build_parser()
    # We need a real file for parse_file_path inside cmd_run, but here we only
    # test argument parsing, not execution.
    args = parser.parse_args(["run", "server.py"])
    assert args.port == 50051
    assert args.server_spec == "server.py"


def test_cli_run_custom_port():
    from mcp_grpc.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["run", "server.py", "--port", "9090"])
    assert args.port == 9090


def test_cli_run_custom_port_short_flag():
    from mcp_grpc.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["run", "server.py", "-p", "7777"])
    assert args.port == 7777


def test_cli_version_subcommand():
    from mcp_grpc.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["version"])
    assert args.command == "version"
