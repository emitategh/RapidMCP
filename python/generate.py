"""Generate Python gRPC stubs from proto/mcp.proto."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PROTO_DIR = ROOT / "proto"
PROTO_FILE = PROTO_DIR / "mcp.proto"
OUT_DIR = Path(__file__).parent / "src" / "fastermcp" / "_generated"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "__init__.py").write_text("")

    cmd = [
        sys.executable, "-m", "grpc_tools.protoc",
        f"--proto_path={PROTO_DIR}",
        f"--python_out={OUT_DIR}",
        f"--grpc_python_out={OUT_DIR}",
        f"--pyi_out={OUT_DIR}",
        str(PROTO_FILE),
    ]
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    # grpc_tools generates bare `import mcp_pb2` which breaks when the file
    # lives inside a package. Rewrite to a relative import.
    grpc_file = OUT_DIR / "mcp_pb2_grpc.py"
    text = grpc_file.read_text()
    text = text.replace("import mcp_pb2 as mcp__pb2", "from fastermcp._generated import mcp_pb2 as mcp__pb2")
    grpc_file.write_text(text)
    print("Fixed import in mcp_pb2_grpc.py")

    print("Proto generation complete.")


if __name__ == "__main__":
    main()
