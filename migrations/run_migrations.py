#!/usr/bin/env python3
"""Migration runner — idempotent, versioned, with dry-run.

Each migration file NNN_name.py must define:
  VERSION = <int>
  def migrate(ctx: dict) -> None   — must be idempotent (safe to re-run)
  def check(ctx: dict) -> bool     — returns True if already applied (optional)

The optional check() lets a migration skip itself if its work is already done,
which handles partial failures gracefully: if migration 009 creates a Qdrant
collection then crashes, on re-run check() sees the collection exists and skips.
"""

import importlib.util
import json
import os
import pathlib
import sys
from typing import Any

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_BASE_DIR = pathlib.Path(os.getenv("BASE_DIR", "/opt/nanobot-stack"))


def load_migration(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def get_current_version(vf: pathlib.Path) -> int:
    if vf.exists():
        try:
            return int(vf.read_text().strip())
        except (ValueError, OSError):
            return 0
    return 0


def discover_migrations() -> list[tuple[int, pathlib.Path]]:
    migs = []
    for p in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.py")):
        try:
            migs.append((int(p.name[:3]), p))
        except ValueError:
            continue
    return migs


def build_context(dry_run: bool = False) -> dict[str, Any]:
    base = DEFAULT_BASE_DIR
    return {
        "base_dir": str(base),
        "rag_home": str(base / "rag-bridge"),
        "nanobot_home": str(base / "nanobot"),
        "mcp_home": str(base / "rag-mcp"),
        "qdrant_config_dir": os.getenv("QDRANT_CONFIG_DIR", "/etc/qdrant"),
        "langfuse_dir": os.getenv("LANGFUSE_DIR", "/opt/docker/langfuse"),
        "version_file": str(base / ".version"),
        "dry_run": dry_run,
    }


def run(dry_run: bool = False, target_version: int | None = None) -> list[dict[str, Any]]:
    ctx = build_context(dry_run=dry_run)
    vf = pathlib.Path(ctx["version_file"])
    current = get_current_version(vf)
    results = []

    for ver, path in discover_migrations():
        if ver <= current:
            continue
        if target_version and ver > target_version:
            break

        prefix = "[DRY-RUN] " if dry_run else ""
        print(f"{prefix}Migration {ver}: {path.stem}")

        try:
            mod = load_migration(path)
            assert hasattr(mod, "VERSION") and mod.VERSION == ver
            assert hasattr(mod, "migrate")

            # Idempotency check: if check() exists and returns True, skip
            if hasattr(mod, "check") and not dry_run:
                if mod.check(ctx):
                    print(f"  → already applied (idempotent check)")
                    results.append({"version": ver, "name": path.stem, "status": "already_applied"})
                    continue

            if not dry_run:
                mod.migrate(ctx)

            results.append({"version": ver, "name": path.stem, "status": "ok"})
            print(f"  → {'would apply' if dry_run else 'applied'}")
        except Exception as e:
            results.append({"version": ver, "name": path.stem, "status": "error", "error": str(e)})
            print(f"  → ERROR: {e}")
            if not dry_run:
                break

    # Update version to highest successful migration
    if not dry_run:
        applied = [r for r in results if r["status"] in ("ok", "already_applied")]
        if applied:
            vf.write_text(str(applied[-1]["version"]))

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--target", type=int)
    args = parser.parse_args()
    results = run(dry_run=args.dry_run, target_version=args.target)
    if not results:
        print("No pending migrations.")
