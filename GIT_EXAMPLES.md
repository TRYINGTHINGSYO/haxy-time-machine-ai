# Source-grounded Git example

Real repository: xit-vcs/xitui, commit `e672f11432f90d3d9fc62ebcd9e9605271f027b8`, author radar roark. The indexed message is “let scrollToRect work after clearGrid”.

Question: **How did scrollToRect change?**

Verified observation: `src/widget.zig`, added lines 1456–1459 at that exact commit, introduces a `viewport: ?layout.Size = null` field. The accompanying source comment states that it survives clearGrid so scrollToRect can work while a parent rebuilds the scroll. Added line 1609 assigns the content width/height to that field.

Accepted local model interpretation:

> scrollToRect was modified to use a viewport field that persists after clearGrid, unlike the grid layout.

This interpretation is supported by the referenced comment and field. It is not an independently executed runtime test. The application does not claim that this proves all scrolling bugs were fixed. The original runtime answer is excluded from the public release; this attributed walkthrough records the accepted explanation.

First clone and index the public xitui revision using the initialization commands in GIT_INTELLIGENCE.md. Then inspect the sources locally:

```powershell
python -m htmai.app --repo xitui git-show e672f114
python -m htmai.app --repo xitui git-ask "How did scrollToRect change?"
```

In the dashboard use `use xitui`, then `git-ask How did scrollToRect change?`, then `cite N` for a numbered source. After indexing, code excerpts and original diffs are stored offline in your local database. A synthetic test repository also verifies deleted-line citations against the old parent, exact added-line offsets, rename snapshots and separate merge-parent views. No test executes the repository's code.
