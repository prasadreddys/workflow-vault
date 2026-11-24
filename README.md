# Workflow Vault

A versioned vault of tested, reusable AI-agent workflows. Free, open source,
stdlib-only Python 3.12, local files only — no network, no API keys, no paid
services.

## The problem

Everyone hoards prompt chains and agent workflows in scattered notes that
rot. Nobody knows which version works, what it costs, or whether it still
passes its tests. Workflow Vault is the local vault: workflows are versioned
JSON files with SHA-256 hashes, declared test cases, and a run log — so a
workflow is only **trusted** when its tests pass on this machine.

## What "trusted" means

A workflow in the vault carries a test status:

- `untested` — added but never run through `workflow test`
- `passed` — every declared test passed on this machine, last time `test` ran
- `failed` — at least one declared test failed

`workflow run` refuses to run a `failed` workflow unless you pass `--force`.
`workflow verify` re-hashes the stored file and re-validates the schema, so
an edit after adding shows up as `CHANGED` or a schema failure. Trust is
earned by a passing test run here, not by a claim in the notes.

## Workflow file format

A workflow is a JSON object. Exact schema:

```json
{
  "name": "summarize-file",
  "version": "1.0.0",
  "description": "Count the lines of a text file and show its first line.",
  "tags": ["text", "demo"],
  "inputs": {
    "file": {"required": true, "description": "path to a text file"}
  },
  "steps": [
    {"id": "count",
     "run": "wc -l {{inputs.file}}",
     "expect": {"exit_code": 0, "stdout_contains": ""}},
    {"id": "head",
     "run": "head -n 1 {{inputs.file}}",
     "expect": {"exit_code": 0}}
  ],
  "tests": [
    {"name": "counts lines",
     "inputs": {"file": "examples/sample.txt"},
     "expect_steps": {
       "count": {"exit_code": 0, "stdout_contains": "3"},
       "head": {"exit_code": 0, "stdout_contains": "Alpha"}}}
  ]
}
```

Rules:

- `name` must match `^[a-z0-9][a-z0-9-]*$`.
- `version` must be semver `X.Y.Z`.
- `steps` is a non-empty list. Every step needs a unique `id`, a non-empty
  `run` command, and an `expect` block (`exit_code` integer,
  `stdout_contains` optional string).
- Every test needs a `name`, `inputs` covering all required inputs, and
  `expect_steps` referencing real step ids.
- `{{inputs.<name>}}` is the only template syntax. An unknown variable is a
  validation error, never silently blank.
- Values are substituted with `shlex.quote`, so a value with spaces or shell
  metacharacters is passed as one safe argument.

## Usage

```bash
# write the two sample workflows + fixture into a directory
python3 -m workflow init --dir demo

# add a workflow to the vault (validates, hashes, records "untested")
python3 -m workflow add --vault demo --file demo/sample-workflow.json

# list stored workflows, optionally filtered by tag
python3 -m workflow list --vault demo [--tag text]

# show a workflow and its manifest entry (latest version if omitted)
python3 -m workflow show --vault demo --name summarize-file [--version 1.0.0]

# run the declared tests; manifest status becomes passed or failed
python3 -m workflow test --vault demo --name summarize-file

# run the steps with real inputs (refuses a failed workflow without --force)
python3 -m workflow run --vault demo --name summarize-file --input file=examples/sample.txt [--force]

# re-hash and re-validate a stored workflow
python3 -m workflow verify --vault demo --name summarize-file
```

Steps run as shell commands (so pipes and redirects work), with a clean
minimal environment (PATH only), a 10-second timeout per step, and the vault
directory as the working directory. Run logs land in
`<vault>/runs/<name>-<version>-<timestamp>.json` with per-step exit code,
stdout/stderr (truncated to 2000 chars), duration, and pass/fail.

## Vault layout

```
<vault>/
  manifest.json                  # one entry per name+version: sha256, added, status
  <name>/<version>.json          # the workflow file, verbatim
  runs/<name>-<version>-<ts>.json
```

## Tests

```bash
./run_tests.sh   # unit suite + demo e2e (add -> test -> run -> verify)
```

## Honest limitations

- Workflows run shell commands from files you add — only add workflows you
  trust, from sources you trust. There is no sandbox: a malicious workflow
  can run anything your user account can run. The vault records trust, it
  does not create it.
- "Trusted" means "its declared tests passed on this machine, just now". It
  says nothing about whether the workflow is correct, safe, or still valid
  after its inputs or tools change.
- Steps run with a 10-second timeout each and a minimal PATH; commands that
  need a login, a network service, or a GUI will fail.
- One writer at a time: plain local files, no locking or multi-machine sync.
- Run logs truncate stdout/stderr at 2000 characters; the full output is not
  kept.
