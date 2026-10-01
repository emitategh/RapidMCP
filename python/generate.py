"""Generate Python gRPC stubs from the files in proto/.

Usage:
    python generate.py                 # every proto
    python generate.py mcp_v2.proto    # only the named ones
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PROTO_DIR = ROOT / "proto"
OUT_DIR = Path(__file__).parent / "src" / "rapidmcp" / "_generated"
PROTO_FILES = ["mcp.proto", "mcp_v2.proto"]


def generate(proto_name: str) -> None:
    cmd = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"--proto_path={PROTO_DIR}",
        f"--python_out={OUT_DIR}",
        f"--grpc_python_out={OUT_DIR}",
        f"--pyi_out={OUT_DIR}",
        str(PROTO_DIR / proto_name),
    ]
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    # grpc_tools generates a bare `import <stem>_pb2`, which breaks when the
    # file lives inside a package. Rewrite it to an absolute package import.
    stem = Path(proto_name).stem
    module = f"{stem}_pb2"
    alias = module.replace("_", "__")
    grpc_file = OUT_DIR / f"{module}_grpc.py"
    text = grpc_file.read_text()
    text = text.replace(
        f"import {module} as {alias}", f"from rapidmcp._generated import {module} as {alias}"
    )
    grpc_file.write_text(text)
    print(f"Fixed import in {grpc_file.name}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    init = OUT_DIR / "__init__.py"
    if not init.exists():
        init.write_text("")
    for name in sys.argv[1:] or PROTO_FILES:
        generate(name)
    print("Proto generation complete.")


if __name__ == "__main__":
    main()
