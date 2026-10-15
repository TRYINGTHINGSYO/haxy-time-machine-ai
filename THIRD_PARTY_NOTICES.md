# Attribution and demo-data provenance

Haxy Time Machine AI is an independent tool inspired by [Haxy](https://github.com/xit-vcs/haxy). It is not an official Haxy project and is not affiliated with Haxy or xit-vcs.

The application uses native Git subprocesses, Python's standard library (including SQLite), and optionally a separately installed Ollama server. These programs and any selected model retain their own licenses; their binaries, model weights and runtime data are not distributed here.

The original application was developed for this project. Its original code, documentation and fictional demo fixture are released under the root MIT license at the owner's instruction. The Haxy event format informed the parser; this does not claim ownership of the format, platform, user reports or third-party repositories.

The historical Git Intelligence walkthrough discusses public [xitui](https://github.com/xit-vcs/xitui) code by its contributors. xitui is MIT/Expat licensed. Its notice is preserved in XITUI_LICENSE.txt and applies to any referenced source excerpts. Do not attribute those changes or their comments to this application.

events.json is a newly authored synthetic fixture: fictional reports, comments, author, IDs and commit hashes. It preserves event update/removal patterns for regression coverage. It does not contain copied public user comments, personal email addresses, attachments or real repository history. Synthetic IDs are not resolvable Git objects. The source field explicitly identifies the fixture as synthetic.

The recorded benchmark aggregate describes an earlier local run against public xitui history. It contains only timing/resource measurements and configuration, not a database, source-code corpus, model conversation, private local path, hostname or credential. Real datasets can be initialized locally using the documented explicit Git/Haxy commands, subject to their original licenses and access policies. Indexing data does not grant permission to republish it.
