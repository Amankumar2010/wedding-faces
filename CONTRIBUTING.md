# Contributing

Start with a small, focused issue or pull request. See the README for setup.

## Protect people's data

Use synthetic fixtures. Never commit photos, face crops, names, embeddings, catalogs, source paths, screenshots from a personal library, or logs that contain them. Do not attach those items to issues. A face embedding is sensitive data even when it does not look like a photograph.

Run `python3 -m unittest discover -s tests` and `python3 scripts/package_source.py` before submitting. Add new public source files explicitly to `PUBLIC_FILES.json` only after checking their contents. The allowlist does not automatically make a file safe.

For Swift changes, run `bash scripts/build.sh` on macOS. Explain what changed and what you verified. Recognition changes need synthetic or appropriately licensed, consented evaluation data; don't claim accuracy from anecdotes.

Code contributions use the repository's MIT license. Third-party assets and models must retain their licenses and provenance.
