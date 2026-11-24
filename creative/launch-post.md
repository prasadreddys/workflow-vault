# Workflow Vault launch post — DRAFT, not published

Status: draft for the user's approval. Nothing posted yet. Repo not created, nothing pushed.

Rules for this post: only measured numbers, no user/sales/revenue claims, no invented quotes.

## X / Threads version

Your best agent workflow lives in a note somewhere. Nobody knows which version works, or whether it still passes its tests.

Workflow Vault is the local vault: versioned workflows, SHA-256 hashes, declared tests, and a run log. A workflow is only "trusted" when its tests pass on your machine.

Free, open source, stdlib-only Python, local files only, no sign-up.

## Longer version (README / Product Hunt style)

Prompt chains rot. Workflow Vault stops the rot: store a workflow as a versioned JSON file, declare its test cases, and the vault runs them — exit codes and expected output compared step by step — and marks the workflow passed, failed, or untested. `verify` re-hashes the file and re-validates the schema, so a one-byte edit is caught. `run` refuses a failed workflow unless you pass `--force`, and every run writes a log with per-step exit codes, output, and timings.

- Demo (fixture: 3 lines, 3 words): summarize-file 1.0.0 test PASS (count "3", head "Alpha"), word-count 1.0.0 test PASS, run log matches hand-computed values, both hashes MATCHED
- Safety: inputs are substituted with shlex quoting (a value like `a; rm -rf /` runs as one quoted argument), 10s timeout per step, minimal PATH, vault dir as cwd
- Honest about what it is: no sandbox — workflows run shell commands as your user, so only add workflows you trust. The vault records trust, it does not create it.

Measured, not estimated: 37 unit tests, all passing. No network, no keys, no paid API.
