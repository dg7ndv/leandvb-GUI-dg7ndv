#!/usr/bin/env bash
# laeuft IM Container (debian:bookworm, armhf): installiert die Werkzeuge, baut datvrx + dvbstool statisch
set -eu
export RUSTFLAGS="-C target-cpu=native"
export DEBIAN_FRONTEND=noninteractive
echo "== $(date +%T) apt: Werkzeuge installieren"
apt-get update -qq
apt-get install -y -qq build-essential cmake git ca-certificates curl pkg-config xz-utils file >/dev/null
echo "== $(date +%T) Rust (rustup, armv7)"
curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-host armv7-unknown-linux-gnueabihf >/dev/null 2>&1
. "$HOME/.cargo/env"
rustc --version; cargo --version; g++ --version | head -1; cmake --version | head -1; uname -m; getconf LONG_BIT
echo "== $(date +%T) cmake konfigurieren"
cmake -S /work/DATVision -B /work/obj-native -DCMAKE_BUILD_TYPE=Release -DDATVRX_TOOLS=ON -DDATVRX_NATIVE=ON -DCMAKE_EXE_LINKER_FLAGS="-static" 2>&1 | tail -8
echo "== $(date +%T) bauen (2 Jobs, nice 19)"
nice -n 19 cmake --build /work/obj-native --parallel 2 2>&1 | tail -n 40
mkdir -p /work/out-native; cp /work/obj-native/datvrx /work/obj-native/dvbstool /work/out-native/
file /work/out-native/datvrx
chown -R "${HOSTUID:-1000}:${HOSTGID:-1000}" /work 2>/dev/null || true
echo "== $(date +%T) FERTIG"
