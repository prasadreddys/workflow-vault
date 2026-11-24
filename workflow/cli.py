"""Command-line interface for Workflow Vault.

Subcommands: add, list, show, test, run, verify, init.
Exit codes: 0 success, 1 a workflow test/run failed, 2 usage or vault error.
"""

import argparse
import json
import sys
from pathlib import Path

from .runner import run_test_case, run_workflow_steps
from .vault import STATUS_FAILED, STATUS_PASSED, Vault, VaultError, short_hash
from .validate import validate_workflow

SAMPLE_WORKFLOW = {
    "name": "summarize-file",
    "version": "1.0.0",
    "description": "Count the lines of a text file and show its first line.",
    "tags": ["text", "demo"],
    "inputs": {
        "file": {"required": True, "description": "path to a text file"}
    },
    "steps": [
        {
            "id": "count",
            "run": "wc -l {{inputs.file}}",
            "expect": {"exit_code": 0, "stdout_contains": ""},
        },
        {
            "id": "head",
            "run": "head -n 1 {{inputs.file}}",
            "expect": {"exit_code": 0},
        },
    ],
    "tests": [
        {
            "name": "counts lines",
            "inputs": {"file": "examples/sample.txt"},
            "expect_steps": {
                "count": {"exit_code": 0, "stdout_contains": "3"},
                "head": {"exit_code": 0, "stdout_contains": "Alpha"},
            },
        }
    ],
}

SECOND_WORKFLOW = {
    "name": "word-count",
    "version": "1.0.0",
    "description": "Count the words in a text file.",
    "tags": ["text", "demo"],
    "inputs": {
        "file": {"required": True, "description": "path to a text file"}
    },
    "steps": [
        {
            "id": "count",
            "run": "wc -w {{inputs.file}}",
            "expect": {"exit_code": 0, "stdout_contains": ""},
        }
    ],
    "tests": [
        {
            "name": "counts words",
            "inputs": {"file": "examples/sample.txt"},
            "expect_steps": {"count": {"exit_code": 0, "stdout_contains": "3"}},
        }
    ],
}

SAMPLE_TXT = "Alpha\nBravo\nCharlie\n"


def _print_json(payload):
    print(json.dumps(payload, indent=2))


def _fail(message):
    print("error: %s" % message, file=sys.stderr)
    return 2


def cmd_add(args):
    vault = Vault(args.vault)
    try:
        entry = vault.add(args.file)
    except VaultError as exc:
        return _fail(str(exc))
    print("added %s %s  sha256 %s" % (entry["name"], entry["version"], short_hash(entry)))
    print("status: %s" % entry["status"])
    return 0


def cmd_list(args):
    vault = Vault(args.vault)
    entries = vault.list_entries(tag=args.tag)
    if not entries:
        print("no workflows in the vault" + (" with tag %r" % args.tag if args.tag else ""))
    else:
        print("%-20s %-9s %-9s %-14s %s" % ("NAME", "VERSION", "STATUS", "SHA256", "DESCRIPTION"))
        for e in entries:
            print(
                "%-20s %-9s %-9s %-14s %s"
                % (e["name"], e["version"], e["status"], short_hash(e), e.get("description", ""))
            )
    _print_json(entries)
    return 0


def cmd_show(args):
    vault = Vault(args.vault)
    try:
        data, entry = vault.get(args.name, args.version)
    except VaultError as exc:
        return _fail(str(exc))
    _print_json({"workflow": data, "manifest": entry})
    return 0


def cmd_test(args):
    vault = Vault(args.vault)
    try:
        data, entry = vault.get(args.name, args.version)
    except VaultError as exc:
        return _fail(str(exc))
    tests = data.get("tests", [])
    if not tests:
        print("workflow %s %s declares no tests; status stays %s"
              % (entry["name"], entry["version"], entry["status"]))
        return 0
    results = []
    all_passed = True
    for test in tests:
        result = run_test_case(data, test, vault.dir)
        results.append(result)
        status = "PASS" if result["passed"] else "FAIL"
        print("test %r: %s" % (test["name"], status))
        for check in result["checks"]:
            if not check["passed"]:
                print(
                    "  step %s: expected exit_code=%s stdout_contains=%r, "
                    "got exit_code=%s stdout=%r%s"
                    % (
                        check["step"],
                        check["expected"].get("exit_code"),
                        check["expected"].get("stdout_contains"),
                        check["actual"].get("exit_code"),
                        check["actual"].get("stdout", "")[:120],
                        " (timed out)" if check["actual"].get("timed_out") else "",
                    )
                )
        if not result["passed"]:
            all_passed = False
    status = STATUS_PASSED if all_passed else STATUS_FAILED
    vault.set_status(entry["name"], entry["version"], status)
    print(
        "%d/%d tests passed — status: %s"
        % (sum(1 for r in results if r["passed"]), len(results), status)
    )
    return 0 if all_passed else 1


def cmd_run(args):
    vault = Vault(args.vault)
    try:
        data, entry = vault.get(args.name, args.version)
    except VaultError as exc:
        return _fail(str(exc))
    if entry["status"] == STATUS_FAILED and not args.force:
        return _fail(
            "workflow %s %s is marked failed in the manifest; "
            "fix it and re-test, or re-run with --force"
            % (entry["name"], entry["version"])
        )
    supplied = {}
    for item in args.input or []:
        if "=" not in item:
            return _fail("--input must be key=value, got %r" % item)
        key, value = item.split("=", 1)
        supplied[key] = value
    for input_name in supplied:
        if input_name not in data.get("inputs", {}):
            return _fail("unknown input %r for workflow %s" % (input_name, entry["name"]))
    missing = [
        name
        for name, spec in data.get("inputs", {}).items()
        if spec.get("required") and name not in supplied
    ]
    if missing:
        return _fail("missing required inputs: %s" % ", ".join(missing))
    results = run_workflow_steps(data, supplied, vault.dir)
    overall = all(r["passed"] for r in results)
    for result in results:
        print(
            "step %s: exit_code=%s %s"
            % (result["id"], result["exit_code"], "PASS" if result["passed"] else "FAIL")
        )
        if result["stdout"]:
            print("  stdout: %r" % result["stdout"][:200])
        if result["stderr"]:
            print("  stderr: %r" % result["stderr"][:200])
    run_log = {
        "name": entry["name"],
        "version": entry["version"],
        "inputs": supplied,
        "steps": [
            {
                "id": r["id"],
                "command": r["command"],
                "exit_code": r["exit_code"],
                "stdout": r["stdout"],
                "stdout_truncated": r["stdout_truncated"],
                "stderr": r["stderr"],
                "stderr_truncated": r["stderr_truncated"],
                "duration_ms": r["duration_ms"],
                "timed_out": r["timed_out"],
                "passed": r["passed"],
            }
            for r in results
        ],
        "passed": overall,
    }
    log_path = vault.record_run(entry["name"], entry["version"], run_log)
    print("overall: %s" % ("PASS" if overall else "FAIL"))
    print("run log: %s" % log_path)
    return 0 if overall else 1


def cmd_verify(args):
    vault = Vault(args.vault)
    try:
        result = vault.verify(args.name, args.version)
    except VaultError as exc:
        return _fail(str(exc))
    print("hash: %s (stored %s, current %s)"
          % ("MATCHED" if result["hash_match"] else "CHANGED",
             result["stored_sha256"][:12], result["current_sha256"][:12]))
    if result["schema_ok"]:
        print("schema: OK")
    else:
        print("schema: FAIL")
        for error in result["schema_errors"]:
            print("  - %s" % error)
    return 0 if (result["hash_match"] and result["schema_ok"]) else 1


def cmd_init(args):
    target = Path(args.dir)
    (target / "examples").mkdir(parents=True, exist_ok=True)
    for filename, workflow in (
        ("sample-workflow.json", SAMPLE_WORKFLOW),
        ("second-workflow.json", SECOND_WORKFLOW),
    ):
        path = target / filename
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(workflow, fh, indent=2)
            fh.write("\n")
        errors = validate_workflow(workflow)
        assert not errors, errors
        print("wrote %s" % path)
    fixture = target / "examples" / "sample.txt"
    with open(fixture, "w", encoding="utf-8") as fh:
        fh.write(SAMPLE_TXT)
    print("wrote %s" % fixture)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="workflow",
        description="Workflow Vault — a versioned vault of tested, reusable AI-agent workflows.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add", help="validate and store a workflow file")
    p.add_argument("--vault", default="./workflow-vault")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", help="list stored workflows")
    p.add_argument("--vault", default="./workflow-vault")
    p.add_argument("--tag")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="show a stored workflow and its manifest entry")
    p.add_argument("--vault", default="./workflow-vault")
    p.add_argument("--name", required=True)
    p.add_argument("--version")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("test", help="run a workflow's declared tests")
    p.add_argument("--vault", default="./workflow-vault")
    p.add_argument("--name", required=True)
    p.add_argument("--version")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("run", help="run a workflow's steps with given inputs")
    p.add_argument("--vault", default="./workflow-vault")
    p.add_argument("--name", required=True)
    p.add_argument("--version")
    p.add_argument("--input", action="append", dest="input")
    p.add_argument("--force", action="store_true",
                   help="run even if the manifest status is failed")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("verify", help="re-hash and re-validate a stored workflow")
    p.add_argument("--vault", default="./workflow-vault")
    p.add_argument("--name", required=True)
    p.add_argument("--version")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("init", help="write the two sample workflows and fixture")
    p.add_argument("--dir", required=True)
    p.set_defaults(func=cmd_init)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
