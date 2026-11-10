"""End-to-end contract tests for ``scripts.backend_staging.execute``.

It uses a fake GitHub/registry client and a local bare Git remote;
no network request or real credential is used.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch


CLOUD = Path(os.environ.get("CLOUD_REPO", Path(__file__).resolve().parents[1]))
SCRIPTS = CLOUD / "scripts"


def load(name: str):
    source = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# These are deliberately loaded in production dependency order: the workflow
# entry point and its tests exercise one implementation, not a test duplicate.
order = load("backend_candidate_order")
values = load("backend_staging_values")
retry = load("backend_staging_retry")
staging = load("backend_staging")

A, B, C = "a" * 40, "b" * 40, "c" * 40
BE = "100-hours-a-week/KTB4-14th-BE"
IMAGE = "mingkiboo/audigo-backend"
DIGEST = "sha256:" + "d" * 64
OTHER_DIGEST = "sha256:" + "e" * 64


def candidate(sha=B, digest=DIGEST, **overrides):
    result = {
        "schema_version": 1,
        "source_repository": BE,
        "source_sha": sha,
        "source_ref": "refs/heads/main",
        "image_repository": IMAGE,
        "image_tag": "sha-" + sha,
        "image_digest": digest,
        "platform": "linux/arm64",
        "workflow_run_id": "123",
        "workflow_run_attempt": "2",
        "workflow_run_url": f"https://github.com/{BE}/actions/runs/123/attempts/2",
    }
    result.update(overrides)
    return result


def zipped(payload, extra=()):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("image-candidate.json", json.dumps(payload))
        for name, content in extra:
            archive.writestr(name, content)
    return out.getvalue()


def compare(base, head, status):
    count = {"ahead": (1, 0), "behind": (0, 1), "identical": (0, 0), "diverged": (1, 1)}[status]
    return {"status": status, "ahead_by": count[0], "behind_by": count[1],
            "base_commit": {"sha": base}, "merge_base_commit": {"sha": base if status in ("ahead", "identical") else head}}


class Client:
    """Precisely scoped fake of the orchestrator's API/registry boundary."""
    def __init__(self):
        self.run = {"id": 123, "run_attempt": 2, "name": "Publish backend image",
                    "path": ".github/workflows/publish-image.yml", "workflow_id": 9,
                    "event": "push", "head_branch": "main", "status": "completed",
                    "conclusion": "success", "head_sha": B,
                    "repository": {"full_name": BE}, "head_repository": {"full_name": BE}}
        self.workflow = {"id": 9, "path": ".github/workflows/publish-image.yml"}
        self.pages = {1: [{"id": 7, "name": f"image-candidate-{B}-123-2", "expired": False}]}
        self.total_count = 1
        self.archive = zipped(candidate())
        self.main = C
        self.graph = {A: 0, B: 1, C: 2}
        self.manifest_result = {"schemaVersion": 2, "digest": DIGEST,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [{"digest": "sha256:" + "f" * 64,
                           "platform": {"os": "linux", "architecture": "arm64"}}]}
        self.image_result = {"os": "linux", "architecture": "arm64"}
        self.downloads = []

    def api(self, path):
        if path.endswith("/actions/runs/123/attempts/2"):
            return self.run
        if path.endswith("/actions/workflows/publish-image.yml"):
            return self.workflow
        if "/actions/runs/123/artifacts?" in path:
            page = int(path.split("page=")[-1])
            return {"total_count": self.total_count, "artifacts": self.pages.get(page, [])}
        if path.endswith("/commits/main"):
            return {"sha": self.main}
        if "/compare/" in path:
            base, head = path.rsplit("/compare/", 1)[1].split("...")
            if base not in self.graph or head not in self.graph:
                raise RuntimeError("unknown history ref")
            if base == head:
                status = "identical"
            elif self.graph[head] > self.graph[base]:
                status = "ahead"
            elif self.graph[head] < self.graph[base]:
                status = "behind"
            else:
                status = "diverged"
            return compare(base, head, status)
        raise AssertionError("unexpected API path: " + path)

    def download(self, artifact_id):
        self.downloads.append(artifact_id)
        return self.archive

    def manifest(self, reference):
        if self.manifest_result is None:
            raise RuntimeError("manifest not found")
        return self.manifest_result

    def image(self, reference):
        return self.image_result


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote, self.seed, self.dev = self.root / "remote.git", self.root / "seed", self.root / "dev"
        self.git("init", "--bare", "-q", self.remote)
        self.git("init", "-q", self.seed)
        self.git("-C", self.seed, "config", "user.name", "test")
        self.git("-C", self.seed, "config", "user.email", "test@example.test")
        for rel in values.VALUES:
            target = self.seed / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CLOUD / rel, target)
        (self.seed / "unrelated.txt").write_text("base\n")
        self.git("-C", self.seed, "add", ".")
        self.git("-C", self.seed, "commit", "-qm", "base")
        self.git("-C", self.seed, "branch", "-M", "main")
        self.git("-C", self.seed, "remote", "add", "origin", self.remote)
        self.git("-C", self.seed, "push", "-qu", "origin", "main")
        self.git("clone", "-q", self.remote, self.dev)
        self.git("-C", self.dev, "checkout", "-q", "main")
        self.git("-C", self.dev, "config", "user.name", "developer")
        self.git("-C", self.dev, "config", "user.email", "developer@example.test")
        (self.dev / "local-change").write_text("preserve\n")
        self.client = Client()

    def git(self, *args, check=True):
        return subprocess.run(("git", *map(str, args)), text=True, capture_output=True, check=check)

    def head(self):
        return self.git("--git-dir", self.remote, "rev-parse", "main").stdout.strip()

    def remote_file(self, rel):
        return self.git("--git-dir", self.remote, "show", f"main:{rel}").stdout

    def execute(self):
        return staging.execute(self.dev, "123", "2", self.client, temp_parent=self.root)

    def record(self, sha=B, digest=DIGEST):
        values.update(self.seed, candidate(sha, digest))
        self.git("-C", self.seed, "add", ".")
        self.git("-C", self.seed, "commit", "-qm", "record")
        self.git("-C", self.seed, "push", "-q")

    def assert_no_write(self, before):
        self.assertEqual(self.head(), before)
        self.assertEqual((self.dev / "local-change").read_text(), "preserve\n")

    def test_normal_candidate_updates_exactly_the_three_allowed_files(self):
        result = self.execute()
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["decision"], "first-registration")
        self.assertEqual(result["source_sha"], B)
        self.assertEqual(result["image_digest"], DIGEST)
        self.assertEqual(result["workflow_run_id"], "123")
        self.assertEqual(result["workflow_run_attempt"], "2")
        self.assertEqual(result["cloud_commit"], self.head())
        changed = self.git("--git-dir", self.remote, "diff-tree", "--no-commit-id", "--name-only", "-r", "main").stdout.splitlines()
        self.assertEqual(sorted(changed), sorted([*values.VALUES, values.RECORD]))
        self.assertEqual(json.loads(self.remote_file(values.RECORD)), candidate())
        self.assertIn("tag: sha-" + B, self.remote_file(values.VALUES[0]))

    def test_korean_publish_name_updates_staging(self):
        self.client.run["name"] = "BE 이미지 발행"
        self.assertEqual(self.execute()["status"], "published")

    def test_entrypoint_json_published_and_skipped(self):
        env = {"GITHUB_ACTIONS": "true", "RUN_ID": "123", "RUN_ATTEMPT": "2"}
        with patch.dict(os.environ, env), patch.object(staging, "Client", return_value=self.client), \
                patch.object(staging.Path, "cwd", return_value=self.dev):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(staging.main(), 0)
            result = json.loads(output.getvalue())
            self.assertEqual((result["status"], result["cloud_commit"]), ("published", self.head()))
            self.assertEqual((result["workflow_run_id"], result["workflow_run_attempt"]), ("123", "2"))
            self.assertEqual((result["source_sha"], result["image_digest"]), (B, DIGEST))
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(staging.main(), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "unchanged")
            self.assertNotIn("cloud_commit", result)

    def test_rejects_invalid_source_run_before_any_write(self):
        before = self.head()
        original = dict(self.client.run)
        for field, value in (("run_attempt", 3), ("status", "in_progress"),
                             ("conclusion", "failure"), ("event", "pull_request"),
                             ("head_branch", "development"), ("id", 124),
                             ("repository", {"full_name": "other/repository"})):
            with self.subTest(field=field):
                self.client.run = dict(original, **{field: value})
                with self.assertRaises(Exception): self.execute()
                self.assert_no_write(before)
        self.client.run = original

    def test_rejects_wrong_workflow_and_candidate_fields_before_any_write(self):
        before = self.head()
        self.client.workflow["id"] = 8
        with self.assertRaises(Exception): self.execute()
        self.assert_no_write(before)
        self.client.workflow["id"] = 9
        self.client.archive = zipped(candidate(image_repository="not-allowed/example"))
        with self.assertRaises(Exception): self.execute()
        self.assert_no_write(before)

    def test_finds_candidate_on_later_artifact_page(self):
        self.client.pages = {1: [{"id": 6, "name": "other", "expired": False}],
                             2: [{"id": 7, "name": f"image-candidate-{B}-123-2", "expired": False}]}
        self.client.total_count = 2
        self.assertEqual(self.execute()["status"], "published")
        self.assertEqual(self.client.downloads, [7])

    def test_rejects_missing_duplicate_or_expired_artifacts(self):
        for artifacts in ([],
                          [{"id": 7, "name": f"image-candidate-{B}-123-2", "expired": True}],
                          [{"id": 7, "name": f"image-candidate-{B}-123-2", "expired": False},
                           {"id": 8, "name": f"image-candidate-{B}-123-2", "expired": False}]):
            before = self.head(); self.client.pages = {1: artifacts}; self.client.total_count = len(artifacts)
            with self.assertRaises(Exception): self.execute()
            self.assert_no_write(before)

    def test_rejects_unsafe_or_oversized_archive_before_any_write(self):
        before = self.head()
        self.client.archive = zipped(candidate(), [("../escape", "x")])
        with self.assertRaises(Exception): self.execute()
        self.assert_no_write(before)
        oversized = candidate(workflow_run_url="x" * (128 * 1024))
        self.client.archive = zipped(oversized)
        with self.assertRaises(Exception): self.execute()
        self.assert_no_write(before)

    def test_registry_absence_and_non_arm64_reject_before_any_write(self):
        before = self.head(); self.client.manifest_result = None
        with self.assertRaises(Exception): self.execute()
        self.assert_no_write(before)
        self.client.manifest_result = {"schemaVersion": 2, "digest": DIGEST,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [{"digest": "sha256:" + "f" * 64,
                           "platform": {"os": "linux", "architecture": "amd64"}}]}
        with self.assertRaises(Exception): self.execute()
        self.assert_no_write(before)

    def test_stale_and_same_sha_different_digest_do_not_write(self):
        self.record(C); before = self.head()
        self.assertEqual(self.execute()["status"], "stale")
        self.assert_no_write(before)
        # A second independent fixture is unnecessary: current B with other
        # digest must fail, never silently replace the recorded image.
        self.client.main = B
        self.record(B, OTHER_DIGEST); before = self.head()
        with self.assertRaises(Exception): self.execute()
        self.assert_no_write(before)

    def test_push_race_rechecks_latest_main_and_preserves_unrelated_change(self):
        original, raced = retry.Git.run, False
        def race(git, *args, **kwargs):
            nonlocal raced
            if args[:1] == ("push",) and not raced:
                raced = True
                other = self.root / "other"
                self.git("clone", "-q", self.remote, other); self.git("-C", other, "checkout", "-q", "main")
                self.git("-C", other, "config", "user.name", "other"); self.git("-C", other, "config", "user.email", "other@example.test")
                (other / "unrelated.txt").write_text("race-preserved\n")
                self.git("-C", other, "add", "unrelated.txt"); self.git("-C", other, "commit", "-qm", "race"); self.git("-C", other, "push", "-q")
            return original(git, *args, **kwargs)
        with patch.object(retry.Git, "run", race):
            result = self.execute()
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(self.remote_file("unrelated.txt"), "race-preserved\n")


class AdapterTests(unittest.TestCase):
    def test_single_manifest_and_missing_digest(self):
        client = Client()
        client.manifest_result = {"schemaVersion": 2, "digest": DIGEST,
            "mediaType": "application/vnd.oci.image.manifest.v1+json"}
        staging.validate_registry(client, candidate())
        client.image_result = {"os": "linux", "architecture": "amd64"}
        with self.assertRaises(staging.IntegrationError):
            staging.validate_registry(client, candidate())
        client.manifest_result.pop("digest")
        with self.assertRaises(staging.IntegrationError):
            staging.validate_registry(client, candidate())

    def test_download_drops_authorization_on_redirect(self):
        import urllib.request
        from unittest.mock import MagicMock
        first, second = MagicMock(), MagicMock()
        first.open.side_effect = urllib.error.HTTPError("https://api.github.com/mock", 302,
            "Found", {"Location": "https://objects.example.test/signed?secret=opaque"}, None)
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"zip"
        second.open.return_value = response
        with patch.dict(os.environ, {"GH_TOKEN": "dummy-not-a-secret"}), \
             patch.object(urllib.request, "build_opener", side_effect=[first, second]):
            self.assertEqual(staging.Client().download(7), b"zip")
        self.assertEqual(first.open.call_args.args[0].get_header("Authorization"),
                         "Bearer dummy-not-a-secret")
        self.assertIsNone(second.open.call_args.args[0].get_header("Authorization"))

    def test_download_rejects_non_https_redirect(self):
        import urllib.request
        from unittest.mock import MagicMock
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError("https://api.github.com/mock", 302,
            "Found", {"Location": "http://objects.example.test/file"}, None)
        with patch.dict(os.environ, {"GH_TOKEN": "dummy-not-a-secret"}), \
             patch.object(urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(staging.IntegrationError): staging.Client().download(7)
        self.assertEqual(opener.open.call_count, 1)

    def test_cli_summary_sanitizes_credential_bearing_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary"
            stderr = io.StringIO()
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(summary)}), \
                 patch.object(staging, "execute", side_effect=RuntimeError("https://signed.invalid/?token=private")), \
                 redirect_stderr(stderr):
                self.assertEqual(staging.main(), 1)
            self.assertIn("failed", summary.read_text())
            self.assertNotIn("private", summary.read_text() + stderr.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
