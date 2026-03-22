"""Initial v8 migration — no-op marker for the migration framework.

All v7 installations start at version 7. This migration marks the
transition to the migration-aware framework.
"""
VERSION = 8

def migrate(ctx: dict) -> None:
    """No-op: just bumps the version to 8."""
    pass
