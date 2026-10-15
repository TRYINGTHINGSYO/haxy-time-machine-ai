# Additive Git Intelligence migration

Opening an existing memory.sqlite3 adds separate `git_repos`, `git_commits`, `git_reach`, `git_changes`, `git_evidence`, `git_fts`, and `schema_versions` tables. Existing Haxy repositories, events, snapshots, issue states, metadata-only commits, FTS index and AI cache tables are neither dropped nor replaced.

The component schema version is stored in schema_versions. Indexer algorithm settings are stored per commit; a changed algorithm can rebuild that commit's code evidence. An indexing run stages immutable commit data in individual transactions, then switches the active generation in a final transaction. An interrupted run can resume while the last active generation remains available. New index names get an empty Haxy state solely for compatibility with the existing overview/dashboard.

Recommended: close the app and copy data/memory.sqlite3 before upgrading. Replace application files, keep data/, and start normally. No manual SQL or model download is needed. The older executable code ignores the added tables, so its original Haxy functions remain usable with the same database. SQLite integrity and Haxy reconstruction are tested after migration.

Default CPU mode remains unchanged. The preferred model setting is now the installed qwen3.5:4b. Git AI is opt-in; old Haxy ask behavior remains unchanged. Full Git indexing uses the new git-index command; the previous index-commits interface is preserved.
