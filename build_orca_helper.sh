#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
cargo build --locked --release --manifest-path orca_helper/Cargo.toml
mkdir -p bin
cp orca_helper/target/release/crypto-bot-orca-quote bin/orca-quote
