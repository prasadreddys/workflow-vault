"""Workflow Vault tests — stdlib unittest only."""

import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow.cli import SAMPLE_WORKFLOW, SECOND_WORKFLOW, main  # noqa: E402
from workflow.runner import run_step, run_test_case, substitute  # noqa: E402
from workflow.validate import validate_workflow  # noqa: E402
from workflow.vault import Vault, VaultError  # noqa: E402


def fresh_workflow(**overrides):
    wf = copy.deepcopy(SAMPLE_WORKFLOW)
    wf.update(overrides)
    return wf


class ValidationTests(unittest.TestCase):
    def test_valid_workflow_passes(self):
        self.assertEqual(validate_workflow(fresh_workflow()), [])

    def test_bad_names_rejected(self):
        for bad in ["Summarize", "has space", "-leading", "", "under_score", 42, None]:
            wf = fresh_workflow(name=bad)
            self.assertTrue(validate_workflow(wf), "name %r should be rejected" % (bad,))

    def test_bad_versions_rejected(self):
        for bad in ["1.0", "v1.0.0", "1.0.0.0", "1.0.x", 1.0, None]:
            wf = fresh_workflow(version=bad)
            self.assertTrue(validate_workflow(wf), "version %r should be rejected" % (bad,))

    def test_duplicate_step_ids_rejected(self):
        wf = fresh_workflow()
        wf["steps"].append(dict(wf["steps"][0]))
        errors = validate_workflow(wf)
        self.assertTrue(any("duplicate step id" in e for e in errors))

    def test_empty_run_rejected(self):
        for bad in ["", "   ", None]:
            wf = fresh_workflow()
            wf["steps"][0]["run"] = bad
            self.assertTrue(validate_workflow(wf), "run %r should be rejected" % (bad,))

    def test_bad_expect_rejected(self):
        wf = fresh_workflow()
        wf["steps"][0]["expect"] = {"exit_code": "zero"}
        self.assertTrue(validate_workflow(wf))
        wf = fresh_workflow()
        wf["steps"][0]["expect"] = {"exit_code": True}
        self.assertTrue(validate_workflow(wf), "bool is not a valid exit_code")
        wf = fresh_workflow()
        del wf["steps"][0]["expect"]
        self.assertTrue(validate_workflow(wf))

    def test_unknown_template_variable_rejected(self):
        wf = fresh_workflow()
        wf["steps"][0]["run"] = "wc -l {{inputs.missing}}"
        errors = validate_workflow(wf)
        self.assertTrue(any("not a declared input" in e for e in errors))

    def test_non_inputs_template_rejected(self):
        wf = fresh_workflow()
        wf["steps"][0]["run"] = "echo {{env.HOME}}"
        errors = validate_workflow(wf)
        self.assertTrue(any("only template syntax" in e for e in errors))

    def test_test_missing_required_input_rejected(self):
        wf = fresh_workflow()
        wf["tests"][0]["inputs"] = {}
        errors = validate_workflow(wf)
        self.assertTrue(any("missing required input" in e for e in errors))

    def test_test_unknown_step_rejected(self):
        wf = fresh_workflow()
        wf["tests"][0]["expect_steps"] = {"nope": {"exit_code": 0}}
        errors = validate_workflow(wf)
        self.assertTrue(any("unknown step id" in e for e in errors))

    def test_test_undeclared_input_rejected(self):
        wf = fresh_workflow()
        wf["tests"][0]["inputs"]["surprise"] = "x"
        errors = validate_workflow(wf)
        self.assertTrue(any("not declared" in e for e in errors))

    def test_empty_steps_rejected(self):
        wf = fresh_workflow(steps=[])
        self.assertTrue(validate_workflow(wf))

    def test_non_object_rejected(self):
        self.assertTrue(validate_workflow(["not", "a", "dict"]))


class SubstitutionTests(unittest.TestCase):
    def test_values_with_spaces_are_quoted(self):
        out = substitute("cat {{inputs.file}}", {"file": "my file.txt"})
        self.assertEqual(out, "cat 'my file.txt'")

    def test_shell_metacharacters_are_quoted(self):
        out = substitute("echo {{inputs.file}}", {"file": "a; rm -rf /"})
        self.assertNotIn("rm -rf", out.replace("'a; rm -rf /'", ""))
        self.assertIn("'a; rm -rf /'", out)

    def test_missing_value_raises(self):
        with self.assertRaises(KeyError):
            substitute("cat {{inputs.file}}", {})

    def test_quoted_value_runs_as_one_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "my file.txt"
            target.write_text("hello\n")
            result = run_step("wc -l {{inputs.file}}", {"file": str(target)}, tmp)
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("1", result["stdout"])


class RunnerTests(unittest.TestCase):
    def test_step_timeout(self):
        result = run_step("sleep 2", {}, tempfile.gettempdir(), timeout=1)
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["exit_code"])
        self.assertIn("timeout", result["stderr"])

    def test_step_failure_recorded(self):
        result = run_step("exit 3", {}, tempfile.gettempdir())
        self.assertEqual(result["exit_code"], 3)
        self.assertFalse(result["timed_out"])

    def test_stdout_truncated_at_2000_chars(self):
        result = run_step("python3 -c \"print('x'*5000)\"",
                          {}, tempfile.gettempdir())
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(len(result["stdout"]), 2000)
        self.assertTrue(result["stdout_truncated"])


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault_dir = Path(self.tmp.name) / "vault"
        self.vault = Vault(self.vault_dir)
        self.src = Path(self.tmp.name) / "wf.json"
        self.src.write_text(json.dumps(fresh_workflow()))

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_stores_file_and_manifest(self):
        entry = self.vault.add(str(self.src))
        self.assertEqual(entry["name"], "summarize-file")
        self.assertEqual(entry["version"], "1.0.0")
        self.assertEqual(entry["status"], "untested")
        self.assertEqual(len(entry["sha256"]), 64)
        stored = self.vault_dir / "summarize-file" / "1.0.0.json"
        self.assertTrue(stored.exists())
        self.assertEqual(json.loads(stored.read_text()), fresh_workflow())

    def test_add_duplicate_version_rejected(self):
        self.vault.add(str(self.src))
        with self.assertRaises(VaultError):
            self.vault.add(str(self.src))

    def test_add_invalid_workflow_rejected(self):
        bad = fresh_workflow(name="Bad Name")
        self.src.write_text(json.dumps(bad))
        with self.assertRaises(VaultError):
            self.vault.add(str(self.src))

    def test_list_tag_filter(self):
        self.vault.add(str(self.src))
        second = Path(self.tmp.name) / "wf2.json"
        wf2 = copy.deepcopy(SECOND_WORKFLOW)
        wf2["tags"] = ["other"]
        second.write_text(json.dumps(wf2))
        self.vault.add(str(second))
        self.assertEqual(len(self.vault.list_entries()), 2)
        self.assertEqual(len(self.vault.list_entries(tag="text")), 1)
        self.assertEqual(len(self.vault.list_entries(tag="missing")), 0)

    def test_show_latest_version_resolution(self):
        self.vault.add(str(self.src))
        v2 = fresh_workflow(version="1.1.0")
        self.src.write_text(json.dumps(v2))
        self.vault.add(str(self.src))
        data, entry = self.vault.get("summarize-file")
        self.assertEqual(entry["version"], "1.1.0")
        self.assertEqual(data["version"], "1.1.0")
        data, entry = self.vault.get("summarize-file", "1.0.0")
        self.assertEqual(entry["version"], "1.0.0")

    def test_get_unknown_workflow_raises(self):
        with self.assertRaises(VaultError):
            self.vault.get("no-such-workflow")

    def test_verify_matched_on_untouched_file(self):
        self.vault.add(str(self.src))
        result = self.vault.verify("summarize-file")
        self.assertTrue(result["hash_match"])
        self.assertTrue(result["schema_ok"])

    def test_verify_changed_after_one_byte_edit(self):
        self.vault.add(str(self.src))
        stored = self.vault_dir / "summarize-file" / "1.0.0.json"
        text = stored.read_text()
        stored.write_text(text.replace("summarize-file", "summarize-filf", 1))
        result = self.vault.verify("summarize-file")
        self.assertFalse(result["hash_match"])
        self.assertTrue(result["schema_ok"])

    def test_verify_schema_catches_corrupted_file(self):
        self.vault.add(str(self.src))
        stored = self.vault_dir / "summarize-file" / "1.0.0.json"
        data = json.loads(stored.read_text())
        data["version"] = "not-semver"
        stored.write_text(json.dumps(data))
        result = self.vault.verify("summarize-file")
        self.assertFalse(result["hash_match"])
        self.assertFalse(result["schema_ok"])
        self.assertTrue(result["schema_errors"])

    def test_verify_corrupt_json_raises(self):
        self.vault.add(str(self.src))
        stored = self.vault_dir / "summarize-file" / "1.0.0.json"
        stored.write_text("{not json")
        with self.assertRaises(VaultError):
            self.vault.verify("summarize-file")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault_dir = self.root / "vault"
        # fixture the sample tests reference, relative to the vault dir
        (self.vault_dir / "examples").mkdir(parents=True)
        (self.vault_dir / "examples" / "sample.txt").write_text("Alpha\nBravo\nCharlie\n")
        self.src = self.root / "wf.json"
        self.src.write_text(json.dumps(fresh_workflow()))

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(list(argv))
        return code, buf.getvalue()

    def test_cli_test_passing_workflow_marks_passed(self):
        code, out = self.run_cli("add", "--vault", str(self.vault_dir), "--file", str(self.src))
        self.assertEqual(code, 0)
        code, out = self.run_cli("test", "--vault", str(self.vault_dir), "--name", "summarize-file")
        self.assertEqual(code, 0)
        self.assertIn("1/1 tests passed", out)
        _, entry = Vault(self.vault_dir).get("summarize-file")
        self.assertEqual(entry["status"], "passed")

    def test_cli_test_failing_step_marks_failed_and_names_step(self):
        wf = fresh_workflow()
        wf["tests"][0]["expect_steps"]["count"]["stdout_contains"] = "999"
        self.src.write_text(json.dumps(wf))
        self.run_cli("add", "--vault", str(self.vault_dir), "--file", str(self.src))
        code, out = self.run_cli("test", "--vault", str(self.vault_dir), "--name", "summarize-file")
        self.assertEqual(code, 1)
        self.assertIn("step count", out)
        self.assertIn("FAIL", out)
        _, entry = Vault(self.vault_dir).get("summarize-file")
        self.assertEqual(entry["status"], "failed")

    def test_cli_run_refuses_failed_workflow_without_force(self):
        wf = fresh_workflow()
        wf["tests"][0]["expect_steps"]["count"]["stdout_contains"] = "999"
        self.src.write_text(json.dumps(wf))
        self.run_cli("add", "--vault", str(self.vault_dir), "--file", str(self.src))
        self.run_cli("test", "--vault", str(self.vault_dir), "--name", "summarize-file")
        code, out = self.run_cli(
            "run", "--vault", str(self.vault_dir), "--name", "summarize-file",
            "--input", "file=examples/sample.txt")
        self.assertEqual(code, 2)
        self.assertFalse(list((self.vault_dir / "runs").glob("*.json"))
                         if (self.vault_dir / "runs").exists() else False)

    def test_cli_run_with_force_writes_real_run_log(self):
        wf = fresh_workflow()
        wf["tests"][0]["expect_steps"]["count"]["stdout_contains"] = "999"
        self.src.write_text(json.dumps(wf))
        self.run_cli("add", "--vault", str(self.vault_dir), "--file", str(self.src))
        self.run_cli("test", "--vault", str(self.vault_dir), "--name", "summarize-file")
        code, out = self.run_cli(
            "run", "--vault", str(self.vault_dir), "--name", "summarize-file",
            "--input", "file=examples/sample.txt", "--force")
        self.assertEqual(code, 0)
        logs = list((self.vault_dir / "runs").glob("summarize-file-1.0.0-*.json"))
        self.assertEqual(len(logs), 1)
        log = json.loads(logs[0].read_text())
        self.assertEqual(log["steps"][0]["exit_code"], 0)
        self.assertEqual(log["steps"][0]["stdout"], "3 examples/sample.txt\n")
        self.assertEqual(log["steps"][1]["stdout"], "Alpha\n")
        self.assertTrue(log["passed"])

    def test_cli_run_missing_required_input_errors(self):
        self.run_cli("add", "--vault", str(self.vault_dir), "--file", str(self.src))
        code, out = self.run_cli(
            "run", "--vault", str(self.vault_dir), "--name", "summarize-file")
        self.assertEqual(code, 2)

    def test_cli_run_truncates_long_stdout(self):
        wf = fresh_workflow(name="big-output")
        wf["steps"] = [{
            "id": "big",
            "run": "python3 -c \"print('x'*5000)\"",
            "expect": {"exit_code": 0},
        }]
        wf["tests"] = []
        self.src.write_text(json.dumps(wf))
        self.run_cli("add", "--vault", str(self.vault_dir), "--file", str(self.src))
        code, out = self.run_cli(
            "run", "--vault", str(self.vault_dir), "--name", "big-output",
            "--input", "file=examples/sample.txt")
        self.assertEqual(code, 0)
        logs = list((self.vault_dir / "runs").glob("big-output-1.0.0-*.json"))
        log = json.loads(logs[0].read_text())
        self.assertEqual(len(log["steps"][0]["stdout"]), 2000)
        self.assertTrue(log["steps"][0]["stdout_truncated"])


class CliE2eTests(unittest.TestCase):
    def test_examples_add_test_run_verify_match_expected(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            shutil.copytree(project / "examples", vault / "src")
            # init into the vault dir so the fixture lands at examples/sample.txt
            env = dict(os.environ)
            subprocess.run(
                [sys.executable, "-m", "workflow", "init", "--dir", str(vault)],
                cwd=project, check=True, capture_output=True, env=env)
            for name in ("sample-workflow.json", "second-workflow.json"):
                subprocess.run(
                    [sys.executable, "-m", "workflow", "add",
                     "--vault", str(vault), "--file", str(vault / name)],
                    cwd=project, check=True, capture_output=True, env=env)
            for name in ("summarize-file", "word-count"):
                subprocess.run(
                    [sys.executable, "-m", "workflow", "test",
                     "--vault", str(vault), "--name", name],
                    cwd=project, check=True, capture_output=True, env=env)
            run = subprocess.run(
                [sys.executable, "-m", "workflow", "run",
                 "--vault", str(vault), "--name", "summarize-file",
                 "--input", "file=examples/sample.txt"],
                cwd=project, check=True, capture_output=True, text=True, env=env)
            self.assertIn("overall: PASS", run.stdout)
            subprocess.run(
                [sys.executable, "-m", "workflow", "verify",
                 "--vault", str(vault), "--name", "summarize-file"],
                cwd=project, check=True, capture_output=True, env=env)
            logs = list((vault / "runs").glob("summarize-file-1.0.0-*.json"))
            self.assertEqual(len(logs), 1)
            log = json.loads(logs[0].read_text())
            expected = json.loads((project / "examples" / "expected-run.json").read_text())
            want = expected["expected_run"]
            self.assertTrue(log["passed"] is want["passed"])
            for got, step in zip(log["steps"], want["steps"]):
                self.assertEqual(got["id"], step["id"])
                self.assertEqual(got["command"], step["command"])
                self.assertEqual(got["exit_code"], step["exit_code"])
                self.assertEqual(got["stdout"], step["stdout"])
                self.assertEqual(got["passed"], step["passed"])


if __name__ == "__main__":
    unittest.main()
