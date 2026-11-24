"""Validation for Workflow Vault workflow files.

A workflow is a JSON object with this exact schema::

    {
      "name": "summarize-file",            # ^[a-z0-9][a-z0-9-]*$
      "version": "1.0.0",                  # semver X.Y.Z
      "description": "Count and preview a text file.",
      "tags": ["text"],                    # optional, list of strings
      "inputs": {                          # declared inputs
        "file": {"required": true, "description": "path to a text file"}
      },
      "steps": [                           # at least one step
        {"id": "count",
         "run": "wc -l {{inputs.file}}",
         "expect": {"exit_code": 0, "stdout_contains": ""}}
      ],
      "tests": [                           # optional, list of test cases
        {"name": "counts lines",
         "inputs": {"file": "examples/sample.txt"},
         "expect_steps": {"count": {"exit_code": 0, "stdout_contains": "3"}}}
      ]
    }

``{{inputs.<name>}}`` is the only template syntax. A template variable that
is not a declared input is a validation error, never silently blank.
``validate_workflow`` returns a list of error strings; an empty list means
the workflow is valid.
"""

import re

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
INPUT_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
TEMPLATE_RE = re.compile(r"\{\{([^{}]+)\}\}")
INPUT_TEMPLATE_RE = re.compile(r"^inputs\.([a-zA-Z_][a-zA-Z0-9_]*)$")


def _is_int(value):
    """True for real ints (booleans are not ints here)."""
    return isinstance(value, int) and not isinstance(value, bool)


def extract_template_vars(command: str):
    """Return the list of variable names used in ``{{...}}`` templates."""
    return [m.group(1).strip() for m in TEMPLATE_RE.finditer(command)]


def validate_workflow(data):
    """Validate a parsed workflow dict. Return a list of error strings."""
    errors = []
    if not isinstance(data, dict):
        return ["workflow must be a JSON object"]

    # --- name ---
    name = data.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        errors.append(
            "name must be a string matching ^[a-z0-9][a-z0-9-]*$ "
            "(lowercase letters, digits, hyphens; must start with a letter or digit)"
        )

    # --- version ---
    version = data.get("version")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        errors.append("version must be a semver string X.Y.Z, e.g. '1.0.0'")

    # --- description ---
    if not isinstance(data.get("description"), str):
        errors.append("description must be a string")

    # --- tags ---
    tags = data.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        errors.append("tags must be a list of strings")

    # --- inputs ---
    inputs = data.get("inputs", {})
    if not isinstance(inputs, dict):
        errors.append("inputs must be an object mapping input names to specs")
        inputs = {}
    for input_name, spec in inputs.items():
        if not INPUT_NAME_RE.match(input_name):
            errors.append(
                "input name %r must be a valid identifier "
                "(letters, digits, underscore; must not start with a digit)" % input_name
            )
        if not isinstance(spec, dict):
            errors.append("input %r must be an object with required/description" % input_name)
            continue
        if not isinstance(spec.get("required"), bool):
            errors.append("input %r: 'required' must be true or false" % input_name)
        if not isinstance(spec.get("description"), str):
            errors.append("input %r: 'description' must be a string" % input_name)

    # --- steps ---
    steps = data.get("steps")
    step_ids = []
    if not isinstance(steps, list) or not steps:
        errors.append("steps must be a non-empty list")
        steps = []
    for i, step in enumerate(steps):
        where = "steps[%d]" % i
        if not isinstance(step, dict):
            errors.append("%s must be an object" % where)
            continue
        step_id = step.get("id")
        if not isinstance(step_id, str) or not NAME_RE.match(step_id):
            errors.append("%s: id must be a string matching ^[a-z0-9][a-z0-9-]*$" % where)
        elif step_id in step_ids:
            errors.append("%s: duplicate step id %r" % (where, step_id))
        else:
            step_ids.append(step_id)
        run = step.get("run")
        if not isinstance(run, str) or not run.strip():
            errors.append("%s: run must be a non-empty command string" % where)
        else:
            for var in extract_template_vars(run):
                match = INPUT_TEMPLATE_RE.match(var)
                if match is None:
                    errors.append(
                        "%s: template {{%s}} is not valid — the only template syntax "
                        "is {{inputs.<name>}}" % (where, var)
                    )
                elif match.group(1) not in inputs:
                    errors.append(
                        "%s: template variable {{inputs.%s}} is not a declared input"
                        % (where, match.group(1))
                    )
        expect = step.get("expect")
        if not isinstance(expect, dict):
            errors.append("%s: expect must be an object with exit_code" % where)
        else:
            if not _is_int(expect.get("exit_code")):
                errors.append("%s: expect.exit_code must be an integer" % where)
            if "stdout_contains" in expect and not isinstance(expect["stdout_contains"], str):
                errors.append("%s: expect.stdout_contains must be a string" % where)

    # --- tests ---
    tests = data.get("tests", [])
    if not isinstance(tests, list):
        errors.append("tests must be a list")
        tests = []
    for i, test in enumerate(tests):
        where = "tests[%d]" % i
        if not isinstance(test, dict):
            errors.append("%s must be an object" % where)
            continue
        if not isinstance(test.get("name"), str) or not test["name"].strip():
            errors.append("%s: name must be a non-empty string" % where)
        test_inputs = test.get("inputs")
        if not isinstance(test_inputs, dict):
            errors.append("%s: inputs must be an object" % where)
            test_inputs = {}
        else:
            for input_name, spec in inputs.items():
                if spec.get("required") and input_name not in test_inputs:
                    errors.append(
                        "%s: missing required input %r" % (where, input_name)
                    )
            for input_name in test_inputs:
                if input_name not in inputs:
                    errors.append(
                        "%s: input %r is not declared in the workflow inputs"
                        % (where, input_name)
                    )
        expect_steps = test.get("expect_steps")
        if not isinstance(expect_steps, dict) or not expect_steps:
            errors.append("%s: expect_steps must be a non-empty object" % where)
            continue
        for step_id, expect in expect_steps.items():
            if step_id not in step_ids:
                errors.append(
                    "%s: expect_steps references unknown step id %r" % (where, step_id)
                )
                continue
            if not isinstance(expect, dict):
                errors.append("%s: expect_steps.%s must be an object" % (where, step_id))
                continue
            if not _is_int(expect.get("exit_code")):
                errors.append(
                    "%s: expect_steps.%s.exit_code must be an integer" % (where, step_id)
                )
            if "stdout_contains" in expect and not isinstance(expect["stdout_contains"], str):
                errors.append(
                    "%s: expect_steps.%s.stdout_contains must be a string"
                    % (where, step_id)
                )

    return errors


def load_workflow_file(path):
    """Load a workflow JSON file. Returns (data, error); error is None on success."""
    import json

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None, "file not found: %s" % path
    except json.JSONDecodeError as exc:
        return None, "invalid JSON in %s: %s" % (path, exc)
    except OSError as exc:
        return None, "cannot read %s: %s" % (path, exc)
    return data, None
