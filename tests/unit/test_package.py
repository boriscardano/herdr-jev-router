from importlib.metadata import version

import herdr_jev_router


def test_package_version_matches_installed_metadata() -> None:
    assert herdr_jev_router.__version__ == version("herdr-jev-router")
