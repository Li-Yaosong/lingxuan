"""Verify that v2 package directories are importable."""


def test_import_protocols() -> None:
    import lingxuan.protocols


def test_import_core() -> None:
    import lingxuan.core


def test_import_adapters() -> None:
    import lingxuan.adapters


def test_import_adapters_onebot() -> None:
    import lingxuan.adapters.onebot


def test_import_adapters_openai() -> None:
    import lingxuan.adapters.openai


def test_import_adapters_storage() -> None:
    import lingxuan.adapters.storage


def test_import_adapters_logging() -> None:
    import lingxuan.adapters.logging


def test_import_config_defaults() -> None:
    import lingxuan.config.defaults


def test_import_settings_defaults_compat() -> None:
    """Backward-compat: lingxuan.settings_defaults still importable."""
    import lingxuan.settings_defaults
