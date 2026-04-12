"""FasterMCP CLI — start a FasterMCP server from the command line.

Usage::

    fastermcp run server.py
    fastermcp run server.py --port 8080
    fastermcp run server.py:my_app
    fastermcp version
"""

from __future__ import annotations

import argparse
import importlib.util
import platform
import sys
from pathlib import Path
from typing import Any

# Names tried in order when no explicit object is given.
_CANDIDATE_NAMES = ("mcp", "server", "app")


# ---------------------------------------------------------------------------
# Helpers (importable for testing)
# ---------------------------------------------------------------------------


def parse_file_path(server_spec: str) -> tuple[Path, str | None]:
    """Split ``server_spec`` into a file path and an optional object name.

    Handles::

        "server.py"          -> (Path("server.py"), None)
        "server.py:my_app"   -> (Path("server.py"), "my_app")
        "C:\\path\\srv.py:app" -> (Path("C:\\path\\srv.py"), "app")  # Windows drive

    Returns:
        (resolved_path, object_name_or_None)

    Raises:
        SystemExit(1) if the file does not exist or is not a regular file.
    """
    # Windows drive letters look like "C:\..." — skip the first colon.
    has_drive = len(server_spec) > 1 and server_spec[1] == ":"
    rest = server_spec[2:] if has_drive else server_spec

    if ":" in rest:
        # rsplit on the *last* colon after the drive letter
        prefix, obj = server_spec.rsplit(":", 1)
        file_str, server_object = prefix, obj
    else:
        file_str, server_object = server_spec, None

    file_path = Path(file_str).expanduser().resolve()

    if not file_path.exists():
        print(f"error: file not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    if not file_path.is_file():
        print(f"error: not a file: {file_path}", file=sys.stderr)
        sys.exit(1)

    return file_path, server_object or None


def import_server(file: Path, server_object: str | None = None) -> Any:
    """Import a ``FasterMCP`` instance from *file*.

    If *server_object* is given, that attribute is returned directly.
    Otherwise the module is searched for the first attribute named
    ``mcp``, ``server``, or ``app`` (in that order).

    Returns:
        The discovered server object.

    Raises:
        SystemExit(1) on any import or discovery error.
    """
    # Make the file's directory importable so relative imports work.
    file_dir = str(file.parent)
    if file_dir not in sys.path:
        sys.path.insert(0, file_dir)

    spec = importlib.util.spec_from_file_location("_fastermcp_server", file)
    if not spec or not spec.loader:
        print(f"error: could not load module from {file}", file=sys.stderr)
        sys.exit(1)

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:
        print(f"error: failed to import {file}: {exc}", file=sys.stderr)
        sys.exit(1)

    if server_object:
        obj = getattr(module, server_object, None)
        if obj is None:
            print(
                f"error: object '{server_object}' not found in {file}",
                file=sys.stderr,
            )
            sys.exit(1)
        return obj

    # Auto-discover by trying candidate names.
    for name in _CANDIDATE_NAMES:
        if hasattr(module, name):
            return getattr(module, name)

    print(
        f"error: no FasterMCP instance found in {file}.\n"
        f"Use a standard variable name ({', '.join(_CANDIDATE_NAMES)}) "
        "or specify one with file.py:object syntax.",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> None:
    """Implement ``fastermcp run``."""
    from fastermcp import FasterMCP

    file, server_object = parse_file_path(args.server_spec)
    server = import_server(file, server_object)

    if not isinstance(server, FasterMCP):
        print(
            f"error: object is {type(server).__name__!r}, expected FasterMCP",
            file=sys.stderr,
        )
        sys.exit(1)

    port: int = args.port
    print(f"Starting FasterMCP '{server.name}' on port {port} …", flush=True)
    server.run(port=port)


def cmd_version(_args: argparse.Namespace) -> None:
    """Implement ``fastermcp version``."""
    from fastermcp import __version__  # type: ignore[attr-defined]

    print(f"FasterMCP {__version__}")
    print(f"Python     {platform.python_version()}")
    print(f"Platform   {platform.platform()}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fastermcp",
        description="FasterMCP — gRPC-native MCP server toolkit.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # ── run ──────────────────────────────────────────────────────────────────
    run_p = sub.add_parser("run", help="Start a FasterMCP server.")
    run_p.add_argument(
        "server_spec",
        metavar="FILE[:OBJECT]",
        help=(
            "Python file to run, optionally with :object suffix. "
            "When no object is given, the first of "
            f"({', '.join(_CANDIDATE_NAMES)}) found in the module is used."
        ),
    )
    run_p.add_argument(
        "--port",
        "-p",
        type=int,
        default=50051,
        metavar="PORT",
        help="gRPC port to listen on (default: 50051).",
    )
    run_p.set_defaults(func=cmd_run)

    # ── version ──────────────────────────────────────────────────────────────
    ver_p = sub.add_parser("version", help="Show version information.")
    ver_p.set_defaults(func=cmd_version)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """CLI entry point registered as ``fastermcp`` in pyproject.toml."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
