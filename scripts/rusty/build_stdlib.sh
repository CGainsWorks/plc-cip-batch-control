#!/usr/bin/env bash
# Build the RuSTy standard library with its mock_time feature.
#
#   scripts/rusty/build_stdlib.sh <rusty-tag> <out-dir>
#
# Needs cargo and the `plc` binary of the same RuSTy release on PATH.
#
# Why build it at all: the released libiec61131std.so times TON, TOF and TP
# with the monotonic wall clock. mock_time is RuSTy's own feature, used by its
# test suite, that swaps that clock for one advanced by calling
# __mock_time_advance_ns. The harness advances it by one scan period per scan,
# so a compiled run is deterministic and an hour of plant runs in seconds.
#
# One change to the upstream build: the library's build.rs links the whole
# compiler in as a Rust library (and with it LLVM) only to compile the
# standard library's ST half. Here build.rs runs the released `plc` binary of
# the same version with the same arguments instead. Nothing else is touched;
# Cargo.lock keeps every crate at the version upstream resolved.
set -euo pipefail

TAG=${1:?usage: build_stdlib.sh <rusty-tag> <out-dir>}
OUT=${2:?usage: build_stdlib.sh <rusty-tag> <out-dir>}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)

if [ "$(plc --version)" != "${TAG#v}" ]; then
    echo "plc on PATH is $(plc --version), expected ${TAG#v}" >&2
    exit 1
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
git clone --quiet --depth 1 --branch "$TAG" https://github.com/PLC-lang/rusty.git "$WORK/rusty"
cd "$WORK/rusty"
git log -1 --format='rusty %H %s'

STDLIB=libs/stdlib
# Drop the compiler as a build dependency...
python3 - "$STDLIB/Cargo.toml" <<'PY'
import re, sys
path = sys.argv[1]
text = open(path).read()
patched = re.sub(r"\[build-dependencies\.plc_driver\]\n(?:[^\[\n].*\n)*", "", text)
assert patched != text, "build-dependencies.plc_driver not found"
open(path, "w").write(patched)
PY
# ...and call the released compiler where build.rs called it as a library.
python3 - "$STDLIB/build.rs" <<'PY'
import sys
path = sys.argv[1]
text = open(path).read()
old = "plc_driver::compile(&args).unwrap();"
new = (
    'let status = Command::new("plc").args(&args[1..]).status().expect("plc");\n'
    '    assert!(status.success(), "plc failed to compile the standard library");'
)
assert old in text, "build.rs no longer calls plc_driver::compile"
open(path, "w").write(text.replace(old, new))
PY
git --no-pager diff --stat

cargo build --release --package iec61131std --features mock_time
cp target/release/libiec61131std.so "$OUT/"
nm -D --defined-only "$OUT/libiec61131std.so" | grep -q __mock_time_advance_ns
echo "built $OUT/libiec61131std.so with mock_time"
