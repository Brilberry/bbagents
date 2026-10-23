"""Package metadata and release tags must agree with the public version."""

import os
from pathlib import Path
import tomllib

import bbagents


def test_package_version_matches_metadata_and_release_tag():
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as file:
        metadata = tomllib.load(file)
    assert metadata["project"]["version"] == bbagents.__version__
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        assert os.environ["GITHUB_REF_NAME"] == f"v{bbagents.__version__}"
