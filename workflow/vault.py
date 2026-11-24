"""The vault: on-disk storage for versioned workflows plus a manifest.

Layout inside a vault directory::

    <vault>/
      manifest.json                 # one entry per stored name+version
      <name>/<version>.json         # the workflow file, verbatim
      runs/<name>-<version>-<timestamp>.json   # run logs

A manifest entry records the workflow's SHA-256 (taken at add time), the
time it was added, and its test status: ``untested`` / ``passed`` / ``failed``.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from .hashing import sha256_file, sha256_short
from .validate import load_workflow_file, validate_workflow

STATUS_UNTESTED = "untested"
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"


def _utcnow_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _timestamp_slug():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


class VaultError(Exception):
    """A user-facing vault error (bad input, missing workflow, ...)."""


class Vault:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.manifest_path = self.dir / "manifest.json"
        self.runs_dir = self.dir / "runs"

    # --- manifest -----------------------------------------------------
    def load_manifest(self):
        if not self.manifest_path.exists():
            return {"workflows": []}
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return {"workflows": []}
        if not isinstance(data, dict) or not isinstance(data.get("workflows"), list):
            return {"workflows": []}
        return data

    def save_manifest(self, manifest):
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
            fh.write("\n")

    def find_entry(self, manifest, name, version):
        for entry in manifest["workflows"]:
            if entry["name"] == name and entry["version"] == version:
                return entry
        return None

    def latest_version(self, manifest, name):
        versions = [e["version"] for e in manifest["workflows"] if e["name"] == name]
        if not versions:
            return None
        return sorted(versions, key=lambda v: tuple(int(p) for p in v.split(".")))[-1]

    # --- storage ------------------------------------------------------
    def workflow_path(self, name, version):
        return self.dir / name / ("%s.json" % version)

    def add(self, source_path):
        """Validate and store a workflow file. Returns the manifest entry."""
        data, error = load_workflow_file(source_path)
        if error is not None:
            raise VaultError(error)
        errors = validate_workflow(data)
        if errors:
            raise VaultError("workflow is invalid:\n  - " + "\n  - ".join(errors))
        name, version = data["name"], data["version"]
        self.dir.mkdir(parents=True, exist_ok=True)
        manifest = self.load_manifest()
        if self.find_entry(manifest, name, version) is not None:
            raise VaultError(
                "workflow %s version %s is already in the vault" % (name, version)
            )
        dest = self.workflow_path(name, version)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(source_path, "rb") as src, open(dest, "wb") as out:
            out.write(src.read())
        entry = {
            "name": name,
            "version": version,
            "description": data.get("description", ""),
            "tags": data.get("tags", []),
            "sha256": sha256_file(dest),
            "added": _utcnow_iso(),
            "status": STATUS_UNTESTED,
            "last_run": None,
            "last_test": None,
        }
        manifest["workflows"].append(entry)
        self.save_manifest(manifest)
        return entry

    def list_entries(self, tag=None):
        manifest = self.load_manifest()
        entries = manifest["workflows"]
        if tag is not None:
            entries = [e for e in entries if tag in e.get("tags", [])]
        return entries

    def get(self, name, version=None):
        """Return (workflow_data, manifest_entry) for a name, latest if no version."""
        manifest = self.load_manifest()
        if version is None:
            version = self.latest_version(manifest, name)
            if version is None:
                raise VaultError("no workflow named %r in the vault" % name)
        entry = self.find_entry(manifest, name, version)
        if entry is None:
            raise VaultError(
                "no workflow %s version %s in the vault" % (name, version)
            )
        path = self.workflow_path(name, version)
        data, error = load_workflow_file(path)
        if error is not None:
            raise VaultError(error)
        return data, entry

    def set_status(self, name, version, status):
        manifest = self.load_manifest()
        entry = self.find_entry(manifest, name, version)
        if entry is None:
            raise VaultError(
                "no workflow %s version %s in the vault" % (name, version)
            )
        entry["status"] = status
        entry["last_test"] = _utcnow_iso()
        self.save_manifest(manifest)
        return entry

    def record_run(self, name, version, run_log):
        """Write a run log and stamp the manifest's last_run. Returns the log path."""
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        slug = _timestamp_slug()
        path = self.runs_dir / ("%s-%s-%s.json" % (name, version, slug))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(run_log, fh, indent=2)
            fh.write("\n")
        manifest = self.load_manifest()
        entry = self.find_entry(manifest, name, version)
        if entry is not None:
            entry["last_run"] = _utcnow_iso()
            self.save_manifest(manifest)
        return path

    # --- verification ---------------------------------------------------
    def verify(self, name, version=None):
        """Recompute the stored file's hash and re-validate its schema.

        Returns a dict with hash_match (MATCHED/CHANGED), schema_ok, errors.
        """
        data, entry = self.get(name, version)
        path = self.workflow_path(entry["name"], entry["version"])
        current = sha256_file(path)
        errors = validate_workflow(data)
        return {
            "name": entry["name"],
            "version": entry["version"],
            "stored_sha256": entry["sha256"],
            "current_sha256": current,
            "hash_match": current == entry["sha256"],
            "schema_ok": not errors,
            "schema_errors": errors,
        }


def short_hash(entry):
    return sha256_short(entry["sha256"])
