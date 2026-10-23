# Publishing bbagents

The `publish.yml` workflow publishes to PyPI only when a GitHub release is
published. Ordinary pushes, tags alone, and draft releases do not upload anything.

## One-time configuration

Configure PyPI Trusted Publishing for owner `Brilberry`, repository `bbagents`,
workflow `publish.yml`, and GitHub environment `pypi`. Create that environment in
GitHub settings; a required reviewer is recommended. No PyPI API token is needed.

## Release

1. Set the new version in `pyproject.toml`, commit, push, and check CI passes.
2. Create a GitHub release targeting that commit with tag `vVERSION` (for example,
   `v0.1.0` for the current version). Review its changes, then publish the release.
3. Watch the **Publish to PyPI** workflow. Approve the `pypi` environment deployment
   if required. It checks the tag/version, runs tests, lint and typing checks,
   builds the wheel and source distribution, checks metadata, and smoke-tests the
   installed wheel before uploading through Trusted Publishing.
4. Verify https://pypi.org/project/bbagents/ and install in a fresh environment:

   ```sh
   python -m pip install bbagents==0.1.0
   python -c "from bbagents import Plan, PlanStep"
   ```

Use a new version for each release: published distribution filenames cannot be
reused. Do not attempt to replace a published release with different contents.
GitHub prereleases also trigger publishing; use a PEP 440 prerelease version such
as `0.2.0rc1` and tag `v0.2.0rc1` when that is intentional.

## Local package checks (no upload)

With the development environment activated:

```sh
python -m pip install build twine
python -m build
python -m twine check --strict dist/*
```

Build from a clean checkout/output directory so old distributions are not mixed
into a new release. The CI job starts with an empty build directory.

See [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/).
