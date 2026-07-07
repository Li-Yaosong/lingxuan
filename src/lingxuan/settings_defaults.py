"""Backward-compat shim: ``import lingxuan.settings_defaults`` still works.

The canonical location is now ``lingxuan.config.defaults``.
"""
from lingxuan.config.defaults import *  # noqa: F401,F403
