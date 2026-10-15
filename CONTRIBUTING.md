# Contributing

Use Python 3.10 or newer and Git. No Python packages are required.

```powershell
python -m unittest discover -v
python -m htmai.app --mode deterministic overview
.\launch.ps1 -Doctor
```

Tests create temporary Git repositories and never execute their source code. Inference unit tests use stubs; they do not download or load a model. Windows is the release-tested platform. The included Actions workflow checks Windows Python 3.12 and 3.14; other platforms are not claimed as release-tested.

Keep code-history citations tied to the correct commit, parent, old/new path and line range. Preserve CPU defaults, local-only inference, deterministic fallback, source-text distrust, bounded indexing and cache bypass behavior. Add reproducible fixtures for bug fixes rather than copying a private repository into a test.

Do not commit data/, databases, local clones, model weights, raw conversations, credentials or personal configuration. The tracked git-benchmarks.json is a reviewed historical aggregate. If publishing new measurements, disclose methodology, actual settings, cache behavior and limitations.
