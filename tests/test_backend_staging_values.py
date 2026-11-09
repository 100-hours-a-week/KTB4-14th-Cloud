import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('values', ROOT / 'scripts/backend_staging_values.py')
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)
C = dict(schema_version=1, source_repository=v.BE, source_sha='a'*40,
         source_ref='refs/heads/main', image_repository=v.IMAGE, image_tag='sha-'+'a'*40,
         image_digest='sha256:'+'b'*64, platform='linux/arm64', workflow_run_id='123',
         workflow_run_attempt='2', workflow_run_url=f'https://github.com/{v.BE}/actions/runs/123/attempts/2')


class ValuesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in v.VALUES:
            target = self.root / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        (self.root / 'production.yaml').write_text('production: untouched\n')
        self.git('init', '-q'); self.git('config', 'user.name', 'test'); self.git('config', 'user.email', 'test@example.test')
        self.commit()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.STDOUT)

    def commit(self):
        self.git('add', '.'); self.git('commit', '-qm', 'fixture')

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file() and '.git' not in p.parts}

    def edit(self, name, fn):
        p = self.root / name; p.write_text(fn(p.read_text()))

    def test_success_no_tag_and_comments(self):
        before = self.snapshot()
        v.update(self.root, C); v.verify(self.root, C)
        for name in v.VALUES:
            after = (self.root / name).read_text()
            self.assertIn('# 스테이징 MySQL', after)
            self.assertEqual(v.parse_yaml(after)[0]['image']['tag'], C['image_tag'])
        self.assertEqual((self.root/'production.yaml').read_bytes(), before['production.yaml'])
        self.assertEqual(json.loads((self.root/v.RECORD).read_text()), C)
        self.git('add', '.'); v.verify(self.root, C)

    def test_existing_tag_and_different_candidates(self):
        for index, name in enumerate(v.VALUES):
            def set_tag(text):
                text = re.sub(r'^  tag:.*\n', '', text, flags=re.MULTILINE)
                return text.replace('  digest:', '  tag: sha-'+str(index)*40+'\n  digest:')
            self.edit(name, set_tag)
        self.commit()
        v.update(self.root, C); v.verify(self.root, C)

    def test_bad_candidate_preserves_files(self):
        for field, value in [('image_digest', 'bad'), ('image_repository', 'evil/repo'), ('source_sha', 'short'),
                             ('workflow_run_attempt', '0'), ('workflow_run_url', 'https://evil.test'), ('schema_version', True)]:
            with self.subTest(field=field):
                c = dict(C); c[field] = value; before = self.snapshot()
                with self.assertRaises(v.ValidationError): v.update(self.root, c)
                self.assertEqual(before, self.snapshot())

    def test_bad_yaml_preserves_files(self):
        original = (self.root/v.VALUES[1]).read_text()
        cases = ['image: []\n', '[]\n', 'image: [broken', original+'image: {}\n',
                 original.replace(v.IMAGE, 'evil/repo'), original.replace('image:\n', 'image:\n  repository: duplicate\n'),
                 original.replace('image:\n', 'image: &shared\n')]
        for text in cases:
            with self.subTest(text=text[:30]):
                (self.root/v.VALUES[1]).write_text(text); self.commit(); before = self.snapshot()
                with self.assertRaises(Exception): v.update(self.root, C)
                self.assertEqual(before, self.snapshot())
                (self.root/v.VALUES[1]).write_text(original); self.commit()

    def test_forbidden_fields(self):
        changes = [lambda s: s.replace('AUDIGO_SQS_ENABLED: "false"', 'AUDIGO_SQS_ENABLED: "true"'),
                   lambda s: s.replace('cpu: 500m', 'cpu: 700m'),
                   lambda s: s+'forbidden: true\n',
                   lambda s: s.replace('replicaCount: 1\n', ''),
                   lambda s: s.replace('  repository:', '  extra: true\n  repository:')]
        v.update(self.root, C)
        original = (self.root/v.VALUES[0]).read_text()
        for fn in changes:
            with self.subTest(change=fn):
                (self.root/v.VALUES[0]).write_text(fn(original))
                with self.assertRaises(v.ValidationError): v.verify(self.root, C)
        (self.root/v.VALUES[0]).write_text(original)
        v.verify(self.root, C)

    def test_forbidden_tracked_untracked_production(self):
        v.update(self.root, C)
        for name in ('production.yaml', 'unexpected.txt'):
            p = self.root/name; old = p.read_bytes() if p.exists() else None
            p.write_text('changed\n')
            with self.assertRaises(v.ValidationError): v.verify(self.root, C)
            if old is None: p.unlink()
            else: p.write_bytes(old)

    def test_deleted_renamed_new_values(self):
        v.update(self.root, C)
        p = self.root/v.VALUES[0]; data = p.read_bytes()
        p.unlink()
        with self.assertRaises(v.ValidationError): v.verify(self.root, C)
        p.write_bytes(data)
        p.rename(self.root/'renamed.yaml')
        with self.assertRaises(v.ValidationError): v.verify(self.root, C)
        (self.root/'renamed.yaml').rename(p)
        self.git('rm', '--cached', v.VALUES[0])
        with self.assertRaises(v.ValidationError): v.verify(self.root, C)

    def test_dirty_checkout_rejected_without_cleanup(self):
        (self.root/'developer-change').write_text('preserve')
        before = self.snapshot()
        with self.assertRaises(v.ValidationError): v.update(self.root, C)
        self.assertEqual(before, self.snapshot())

    def test_record_mismatch(self):
        v.update(self.root, C)
        for text in ('{}', '{', json.dumps(dict(C, workflow_run_id='999'))):
            (self.root/v.RECORD).write_text(text)
            with self.assertRaises(ValueError): v.verify(self.root, C)

    def test_postwrite_failure_rolls_back_all_three(self):
        before = self.snapshot()
        with patch.object(v, 'verify', side_effect=v.ValidationError('injected postwrite failure')):
            with self.assertRaises(v.ValidationError): v.update(self.root, C)
        self.assertEqual(before, self.snapshot())

    def test_replace_failure_rolls_back(self):
        before = self.snapshot(); real = os.replace; count = 0
        def fail_second(src, dst):
            nonlocal count
            count += 1
            if count == 2: raise OSError('injected replace failure')
            return real(src, dst)
        with patch.object(v.os, 'replace', side_effect=fail_second):
            with self.assertRaises(OSError): v.update(self.root, C)
        self.assertEqual(before, self.snapshot())

    def test_staged_forbidden_field_cannot_hide_in_index(self):
        v.update(self.root, C)
        p = self.root/v.VALUES[0]; good = p.read_text()
        p.write_text(good.replace('cpu: 500m', 'cpu: 700m'))
        self.git('add', '.')
        p.write_text(good)
        with self.assertRaises(v.ValidationError): v.verify(self.root, C)

    def test_existing_record_validated(self):
        (self.root/v.RECORD).write_text('{}')
        self.commit(); before = self.snapshot()
        with self.assertRaises(v.ValidationError): v.update(self.root, C)
        self.assertEqual(before, self.snapshot())

    def test_values_mode_change_rejected(self):
        v.update(self.root, C)
        (self.root/v.VALUES[0]).chmod(0o755)
        with self.assertRaises(v.ValidationError): v.verify(self.root, C)

    def test_helm_updated_values(self):
        helm = os.environ.get('HELM_BIN', shutil.which('helm'))
        self.assertTrue(helm, 'Set HELM_BIN to run the required Helm validation')
        v.update(self.root, C)
        for name in v.VALUES:
            output = subprocess.check_output([helm, 'template', 'staging-test', str(ROOT/'apps/chart'), '-f', str(self.root/name)], text=True)
            self.assertIn(v.IMAGE+':'+C['image_tag']+'@'+C['image_digest'], output)


if __name__ == '__main__': unittest.main()
