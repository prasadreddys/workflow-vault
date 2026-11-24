"""Running workflow steps.

Steps are shell commands with ``{{inputs.<name>}}`` placeholders. Values are
substituted with :func:`shlex.quote` so a value containing spaces or shell
metacharacters is passed as a single safe argument — never raw. Commands run
via the shell (so pipes, redirects and ``wc``/``head`` work as written), with
a clean minimal environment (PATH only), a 10-second timeout per step, and
the vault directory as the working directory.
"""

import shlex
import subprocess
import time

from .validate import INPUT_TEMPLATE_RE, TEMPLATE_RE

DEFAULT_TIMEOUT = 10
MAX_OUTPUT_CHARS = 2000
MINIMAL_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin"}


def substitute(command: str, inputs: dict) -> str:
    """Replace ``{{inputs.<name>}}`` with shlex-quoted values.

    Raises KeyError for a variable with no supplied value.
    """
    def replace(match):
        var = match.group(1).strip()
        parsed = INPUT_TEMPLATE_RE.match(var)
        if parsed is None:
            raise ValueError(
                "template {{%s}} is not valid — the only template syntax "
                "is {{inputs.<name>}}" % var
            )
        name = parsed.group(1)
        if name not in inputs:
            raise KeyError("no value supplied for input %r" % name)
        return shlex.quote(str(inputs[name]))

    return TEMPLATE_RE.sub(replace, command)


def run_step(command: str, inputs: dict, cwd, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Run one step command. Returns a result dict.

    Result keys: command (after substitution), exit_code, stdout, stderr,
    duration_ms, timed_out. stdout/stderr are truncated to
    :data:`MAX_OUTPUT_CHARS` characters (with the full length recorded in
    stdout_truncated / stderr_truncated flags).
    """
    substituted = substitute(command, inputs)
    started = time.monotonic()
    try:
        proc = subprocess.run(
            substituted,
            shell=True,
            cwd=str(cwd),
            env=dict(MINIMAL_ENV),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        exit_code = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        exit_code = None
        stdout = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = "timeout after %ds" % timeout
        timed_out = True
    duration_ms = int((time.monotonic() - started) * 1000)
    return {
        "command": substituted,
        "exit_code": exit_code,
        "stdout": stdout[:MAX_OUTPUT_CHARS],
        "stdout_truncated": len(stdout) > MAX_OUTPUT_CHARS,
        "stderr": stderr[:MAX_OUTPUT_CHARS],
        "stderr_truncated": len(stderr) > MAX_OUTPUT_CHARS,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
    }


def step_passes(result: dict, expect: dict) -> bool:
    """True when a step result matches its expect block."""
    if result["timed_out"]:
        return False
    if result["exit_code"] != expect.get("exit_code"):
        return False
    needle = expect.get("stdout_contains")
    if needle is not None and needle != "" and needle not in result["stdout"]:
        return False
    return True


def run_workflow_steps(workflow: dict, inputs: dict, cwd, timeout: int = DEFAULT_TIMEOUT):
    """Run every step of ``workflow`` in order. Returns a list of step results.

    Each result is the :func:`run_step` dict plus ``id`` and ``passed``.
    """
    results = []
    for step in workflow["steps"]:
        result = run_step(step["run"], inputs, cwd, timeout=timeout)
        result["id"] = step["id"]
        result["passed"] = step_passes(result, step.get("expect", {}))
        results.append(result)
    return results


def run_test_case(workflow: dict, test: dict, cwd, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Run one declared test case. Returns a per-test result dict.

    Every step runs with the test's inputs; only the steps named in
    ``expect_steps`` are judged. A test passes when every judged step
    matches its expected exit code and stdout substring.
    """
    inputs = test.get("inputs", {})
    step_results = run_workflow_steps(workflow, inputs, cwd, timeout=timeout)
    by_id = {r["id"]: r for r in step_results}
    checks = []
    passed = True
    for step_id, expect in test.get("expect_steps", {}).items():
        result = by_id.get(step_id)
        if result is None:
            checks.append({
                "step": step_id,
                "passed": False,
                "expected": expect,
                "actual": {"exit_code": None, "stdout": ""},
                "error": "step did not run",
            })
            passed = False
            continue
        ok = step_passes(result, expect)
        if not ok:
            passed = False
        checks.append({
            "step": step_id,
            "passed": ok,
            "expected": expect,
            "actual": {
                "exit_code": result["exit_code"],
                "stdout": result["stdout"],
                "timed_out": result["timed_out"],
            },
        })
    return {
        "name": test.get("name"),
        "passed": passed,
        "checks": checks,
        "steps": step_results,
    }
