# Git Intelligence

Point the existing app at an ordinary local repository. No remote write, checkout, hook, textconv filter, external diff driver, submodule execution, source execution, embedding service or cloud inference is involved. Python standard library, Git and optional local Ollama remain the only requirements.

## Quick start

```powershell
python -m htmai.app --repo my-project git-index C:\path\to\repository --limit 200
python -m htmai.app --repo my-project git-ask "How did parse evolve?" --path src/parser.py
python -m htmai.app --repo my-project git-ask "Why was parse modified?" --path src/parser.py --ai
python -m htmai.app --repo my-project git-search "parse file:src/parser.py"
python -m htmai.app --repo my-project git-show COMMIT_PREFIX
python -m htmai.app --repo my-project git-compare OLD_COMMIT NEW_COMMIT
python -m htmai.app --repo my-project git-timeline
python -m htmai.app git-performance
```

Global options precede subcommands. `--repo` is a local index name, not a remote URL. Paths with spaces should be quoted. Indexing registers a new name automatically; using a different source directory requires a new name. Repeat `git-index` after your normal Git fetch/pull or local work. This command reads the repository and updates only the app's dedicated SQLite database.

Use `--ref branch-name` to choose a branch and `--limit N` to bound its reachable history. Defaults index at most 200 commits; up to 100,000 can be explicitly requested. A second identical indexing run reuses stored commits and diffs. A new commit normally processes only that commit. Changes to indexing settings or shallow availability can require reprocessing. Old generations remain in SQLite; only the latest selected generation participates in retrieval. Neither Git worktrees nor branch refs are changed.

The preserved `index-commits` command is still available for old metadata-only workflows. `git-index` is the comprehensive indexer. Haxy event synchronization and offline issue reconstruction remain separate optional commands (`sync`, `time`, `ask`).

## Interactive terminal section

Run `.\launch.ps1`. Within the dashboard:

```text
git-add my-project C:\path\to\repository
git-search parser
git-ask How did parse evolve?
git-ai Why was parse modified?
cite 1
git-show COMMIT_PREFIX
git-compare OLD_COMMIT NEW_COMMIT
git-timeline
git-performance
monitor
```

`git-ask` is deterministic. `git-ai` explicitly adds interpretation. `ai off` or `mode deterministic` disables it. CPU is the default; partial GPU offload remains optional with the same memory/headroom guards. `model NAME` changes the local model for the session. Preferred installed model is qwen3.5:4b; no model is downloaded.

Search/answer citations are numbered. `cite N` opens the selected source, its full code and the original parent-specific diff. This is an in-terminal inspector, not a web link to the current file. `git-open EVIDENCE_ID` provides the same operation from the CLI. `git-show` displays commit metadata, parent relationships, changed-file records and hunks.

## What is indexed

Full commit SHA, actual parent list, author, author timestamp, full bounded message, changed paths, statuses, old rename paths, patches, hunk headers and individual added/deleted/context lines. Symbols appearing in Git hunk headings also enter the search index. Changes are compared against every available parent, with an explicit parent index in citations. A merge is never represented as a fabricated linear patch.

The default bounds are 100 changed files per parent and 256 KiB per file patch. Oversized, non-UTF-8, binary and submodule cases carry warnings rather than invented code evidence. Git output is spooled and bounded before reading into Python; full repository blobs are not loaded into memory. Rename/copy-only changes include a bounded historical snapshot when available. A shallow commit with an unavailable parent is recorded as such, with no pretend root diff. Empty/binary changes may have metadata but no line citation.

An index is a bounded history window, not necessarily the entire project. The index report exposes shallow status, commit-limit status and warnings. After deepening a shallow clone, reindex to resolve previously unavailable parent comparisons.

## Historical citations

Each source contains full change SHA, repository-relative path, start/end lines, evidence kind (`added`, `deleted`, `snapshot`), parent index and snapshot SHA. Added and context lines resolve against the new snapshot. Deleted lines resolve against the selected old parent and old path. Rename evidence points to the relevant historical name.

The app derives all citation fields and quotes from stored Git evidence. The model selects evidence IDs; it never supplies authoritative file paths or line numbers. Explicit supplied SHA/path/range/side fields, if present, must exactly match. Invented source IDs, references and altered quotations are rejected. The stored offline diff remains inspectable even if the original clone is moved. Direct endpoint comparison needs the original local Git objects; reindex at a new name if the source directory moved.

## Retrieval and interpretation

Retrieval combines FTS5 matching over code, hunk headings and messages; weighted message relevance; path matching; parent/child relationships; explicit commit references; optional date and author filters. For evolution queries it limits concentration in a single commit. Sources are deduplicated. Configure `retrieval_limit` and `evidence_bytes` in config.json (500–6000 evidence bytes); smaller budgets skip oversized sources rather than manufacture partial citation ranges.

```powershell
python -m htmai.app --repo my-project git-ask "parse" --author "A Developer" --since 2026-01-01 --until 2026-10-15 --path src/parser.py
```

Dates use UTC; timestamps are authored times and can be skewed. Graph parent relations, not timestamps, establish ancestry. Keyword retrieval cannot guarantee semantic completeness. Use path filters, explicit SHAs and a larger indexing window for difficult questions.

Answers separate **verified facts**, **interpretations**, **hypotheses**, and **missing information**. Facts are deterministic metadata/code observations. Optional model explanations must select supporting stored evidence. Model interpretations are not promoted to verified facts: correct citations do not prove semantic entailment or runtime correctness. Commit messages provide author-stated intent, not independent proof. Regression candidates are hypotheses until tested; the app never runs repository code or silently claims a causal diagnosis.

Code-history caches hash the selected evidence, generation, question, settings and model digest. New active history or changed context creates a miss. Cache bypass is available to the benchmark and bypasses both reads and writes. Existing Haxy caches remain intact.

## Real sample and performance

The public release includes a synthetic Haxy demo fixture, not a runtime database or real user history. To reproduce the historical xitui example locally:

```powershell
git clone https://github.com/xit-vcs/xitui.git local-repos/xitui
python -m htmai.app --repo xitui git-index local-repos/xitui --ref e672f11432f90d3d9fc62ebcd9e9605271f027b8 --limit 12
```

The clone and generated database stay local and ignored. See [GIT_EXAMPLES.md](GIT_EXAMPLES.md) for the attributed historical walkthrough.

Run `python benchmark_git.py --inference --runs 5`. The benchmark uses one unloaded cold start and five warm uncached requests for each mode, then separate cache reads and deterministic searches. Same prompts and inference settings are used across modes; model/prompt-cache warming is disclosed. Failed/fallback/cached results are excluded from the requested inference groups. It records token rates, sampled model VRAM, device usage, system-wide available RAM, configuration and raw answers. The release aggregate is in `GIT_PERFORMANCE.md` and `git-benchmarks.json`; new local reports are written under ignored `data/benchmarks/`.

## Limits and next steps

No automatic regression proof, general semantic search, remote publishing or arbitrary source execution. Large patches/files and missing objects are explicit gaps. Metadata-only or rename-only matches can be less informative than code edits. Exact source validation does not make every AI interpretation true. Potential improvements include syntax-aware function spans, streamed full-history traversal, richer merge analysis and measured cross-platform model-memory sampling. Use `python -m unittest discover -v` for synthetic Git fixtures plus all original tests.
