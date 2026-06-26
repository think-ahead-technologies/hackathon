# ABOUTME: Emit a clangd compile_commands.json for the firmware sources from this file's own
# ABOUTME: flag rules — so the editor resolves our "..." headers without bear/a real build.

# clangd reads compile_commands.json to know each translation unit's include paths + std. The
# host Makefile already compiles the pure logic with `-iquote include -iquote test`; we mirror
# those flags here and also list the on-target files (device_main.c, the HAL, the loaders) so the
# editor at least resolves *our* headers in them — their BSP/TFLM includes stay unresolved off-target.
#
# Absolute paths make this machine-specific (hence gitignored); regenerate with `make compile-commands`.

import json
import os
import sys
from pathlib import Path

FW = Path(__file__).resolve().parent.parent

C_FLAGS = ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-iquote", "include", "-iquote", "test"]
CXX_FLAGS = ["c++", "-std=c++17", "-iquote", "include"]


def entries():
    for path in sorted(FW.glob("src/*.c")) + sorted(FW.glob("test/*.c")):
        yield path, C_FLAGS
    for path in sorted(FW.glob("src/*.cc")):
        yield path, CXX_FLAGS


def main():
    db = [
        {
            "directory": str(FW),
            "file": str(path),
            "arguments": flags + ["-c", str(path)],
        }
        for path, flags in entries()
    ]
    out = FW / "compile_commands.json"
    out.write_text(json.dumps(db, indent=2) + "\n")
    print(f"wrote {out} ({len(db)} entries)")


if __name__ == "__main__":
    sys.exit(main())
