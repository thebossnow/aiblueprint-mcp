# Releasing

This project publishes to [PyPI](https://pypi.org/project/aiblueprint-mcp/) and
creates a GitHub Release automatically when a `v*` tag is pushed. The workflow is
`.github/workflows/release.yml`.

## One-time setup (before the first release)

PyPI publishing uses **Trusted Publishing** (OIDC), so there is no API token to
manage. Configure it once:

1. Create the project on PyPI (or reserve the name with a manual first upload).
2. Add a trusted publisher: PyPI → project → *Settings → Publishing → Add a new publisher*:
   - **Owner:** `thebossnow`
   - **Repository:** `aiblueprint-mcp`
   - **Workflow filename:** `release.yml`
   - **Environment:** `pypi`
3. In the GitHub repo, create an environment named `pypi`
   (*Settings → Environments → New environment*). Add reviewers if you want a
   manual approval gate before each publish.

## Cutting a release

1. Update `CHANGELOG.md`: move items from `[Unreleased]` into a new version
   section with today's date, and refresh the compare links at the bottom.
2. Bump the version in `pyproject.toml` (and `src/aiblueprint_mcp/__init__.py`
   if it carries `__version__`). Keep them in sync — the release workflow fails
   if the tag doesn't match `pyproject.toml`.
3. Commit on `main` (via PR): `chore: release vX.Y.Z`.
4. Tag and push:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
5. The `Release` workflow builds the sdist + wheel, publishes to PyPI, and
   creates the GitHub Release. Watch it in the Actions tab.

## Versioning

This project follows [Semantic Versioning](https://semver.org/). While the tool
surface is pre-1.0, breaking changes to the MCP tool/operation contract bump the
minor version; additive operations and fixes bump the patch version.
