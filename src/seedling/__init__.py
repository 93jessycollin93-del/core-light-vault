"""Seedling reference implementation.

Pure-Python, user-space only. No kernel hooks, no driver dependencies,
no OS-specific assumptions. Runs identically on Windows, Linux and macOS.
"""
__all__ = ["qpdb", "compression", "seed_store"]
