# Releasing

Unity is installed through HACS from this repository.

1. Run the tests: `python -m pytest -q` (in the `ha-unity` pyenv environment).
2. Bump the version in `custom_components/unity/manifest.json` and
   `pyproject.toml`, and update `CHANGELOG.md`.
3. Commit, tag and push:
   ```bash
   git add -A && git commit -m "Release vX.Y.Z"
   git tag vX.Y.Z && git push && git push --tags
   ```
4. Create a GitHub release for the tag with the changelog entry. HACS offers
   releases as versions; without a release it tracks the default branch.

CI (`.github/workflows/validate.yml`) runs hassfest, the HACS validation and the
tests on every push. The HACS validation needs the brand icons in
`custom_components/unity/brand/`.
