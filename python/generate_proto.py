"""Regenerate proto stubs and fix the grpc_tools import for package layout."""
import subprocess
import sys
from pathlib import Path

PROTO_DIR = Path(__file__).parent.parent / "proto"
OUT_DIR = Path(__file__).parent / "src" / "mcp_grpc" / "_generated"
GRPC_FILE = OUT_DIR / "mcp_pb2_grpc.py"

BROKEN_IMPORT = "import mcp_pb2 as mcp__pb2"
FIXED_IMPORT = "from mcp_grpc._generated import mcp_pb2 as mcp__pb2"


def main():
    # Run protoc
    cmd = [
        sys.executable, "-m", "grpc_tools.protoc",
        f"-I{PROTO_DIR}",
        f"--python_out={OUT_DIR}",
        f"--grpc_python_out={OUT_DIR}",
        str(PROTO_DIR / "mcp.proto"),
    ]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=True)

    # Fix the import
    text = GRPC_FILE.read_text()
    if BROKEN_IMPORT in text:
        text = text.replace(BROKEN_IMPORT, FIXED_IMPORT)
        GRPC_FILE.write_text(text)
        print(f"Fixed import in {GRPC_FILE.name}")
    else:
        print(f"Import already correct in {GRPC_FILE.name}")

    print("Done.")


if __name__ == "__main__":
    main()
