#!/usr/bin/env bash
# Wrapper: runs the watcher with the configured ntfy topic.
set -euo pipefail
cd "$(dirname "$0")"
export NTFY_TOPIC="vk18mgrx7"
exec python3 check.py
