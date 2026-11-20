#!/usr/bin/env bash
# baut datvrx + dvbstool nativ auf einem 64-Bit-Raspberry-Pi (Bookworm): generic (portabel, statisch) und native (fuer diese CPU)
set -eu
B=$HOME/datvrx-dienst/build; cd $B
echo "== $(date +%T) Quelle"; [ -d DATVision/.git ] || git clone -q https://github.com/HB9IIU/DATVision DATVision
( cd DATVision && git fetch -q --all && git checkout -q d4d2d79 && git log -1 --format="%h %ad %s" --date=short | cut -c1-100 )
echo "== $(date +%T) Rust"; if [ ! -x $HOME/.cargo/bin/cargo ]; then curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal >/dev/null 2>&1; fi
. $HOME/.cargo/env; rustc --version; g++ --version | head -1; cmake --version | head -1; uname -m
echo "== $(date +%T) generic bauen"
cmake -S DATVision -B obj -DCMAKE_BUILD_TYPE=Release -DDATVRX_TOOLS=ON -DCMAKE_EXE_LINKER_FLAGS="-static" 2>&1 | tail -3
nice -n 10 cmake --build obj --parallel 3 2>&1 | tail -n 5
cp obj/datvrx out/datvrx.generic; cp obj/dvbstool out/dvbstool
echo "== $(date +%T) native bauen"
RUSTFLAGS="-C target-cpu=native" cmake -S DATVision -B obj-native -DCMAKE_BUILD_TYPE=Release -DDATVRX_TOOLS=ON -DDATVRX_NATIVE=ON -DCMAKE_EXE_LINKER_FLAGS="-static" 2>&1 | tail -3
RUSTFLAGS="-C target-cpu=native" nice -n 10 cmake --build obj-native --parallel 3 2>&1 | tail -n 5
cp obj-native/datvrx out/datvrx.native
file out/datvrx.generic out/datvrx.native | cut -c1-150
echo "== $(date +%T) FERTIG"
