#!/usr/bin/env bash
# Compile plc/ with RuSTy into a shared object the harness can load.
#
#   scripts/rusty/compile_plc.sh <stdlib-dir> [out-dir]
#
# <stdlib-dir> holds the RuSTy standard library built with the mock_time
# feature (scripts/rusty/build_stdlib.sh). The compiler itself is the `plc`
# binary from the pinned plc-compiler release, with the standard library
# declarations it ships in /usr/share/plc/include.
#
# The source is compiled twice from the same files: once to a shared object,
# once to LLVM IR so the loader can read the memory layout the compiler chose
# instead of assuming one. Any diagnostic at all, warning included, fails the
# build: the source has been made warning-clean and should stay that way.
set -euo pipefail

STDLIB_DIR=${1:?usage: compile_plc.sh <stdlib-dir> [out-dir]}
OUT=${2:-build/rusty}
INCLUDE=${PLC_INCLUDE:-/usr/share/plc/include}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)

mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
STDLIB_DIR=$(cd "$STDLIB_DIR" && pwd)
cd "$ROOT"

plc --version
diagnostics=$(plc --check 'plc/**/*.st' -i "$INCLUDE/*.st" --error-format clang 2>&1) || {
    printf '%s\n' "$diagnostics"
    exit 1
}
if [ -n "$diagnostics" ]; then
    printf '%s\n' "$diagnostics"
    echo "RuSTy reported diagnostics; the source is expected to compile clean" >&2
    exit 1
fi

cp "$STDLIB_DIR/libiec61131std.so" "$OUT/libiec61131std.so"
plc 'plc/**/*.st' -i "$INCLUDE/*.st" --shared -o "$OUT/libcip.so" \
    -L "$OUT" -l iec61131std \
    --linker-arg='-rpath=$ORIGIN' \
    --hwmap-file="$OUT/libcip.hwmap.json"
plc 'plc/**/*.st' -i "$INCLUDE/*.st" --ir -o "$OUT/cip.ll"

echo "built $OUT/libcip.so"
