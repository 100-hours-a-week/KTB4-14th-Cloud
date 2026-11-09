import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('order', Path(__file__).resolve().parents[1] / 'scripts/backend_candidate_order.py')
order = importlib.util.module_from_spec(spec)
spec.loader.exec_module(order)
A, B, C = 'a'*40, 'b'*40, 'c'*40
D = 'sha256:' + 'd'*64


def record(sha=B):
    return {'source_sha': sha, 'image_digest': D}


def response(base, head, status):
    counts = {'ahead': (1, 0), 'behind': (0, 1), 'identical': (0, 0), 'diverged': (1, 1)}
    a, b = counts[status]
    merge = base if status in ('ahead', 'identical') else head if status == 'behind' else C
    return {'status': status, 'ahead_by': a, 'behind_by': b,
            'base_commit': {'sha': base}, 'merge_base_commit': {'sha': merge}}


class OrderTests(unittest.TestCase):
    def api(self, status='ahead', main_status='identical'):
        main = B if main_status == 'identical' else A
        def call(path):
            if path.endswith('/commits/main'):
                return {'sha': main}
            if path.endswith(f'/compare/{B}...{main}'):
                return response(B, main, main_status)
            self.assertTrue(path.endswith(f'/compare/{A}...{B}'))
            return response(A, B, status)
        return call

    def test_first_and_same(self):
        self.assertEqual(order.decide(record(), None, self.api()), 'first-registration')
        self.assertEqual(order.decide(record(), record(), self.api()), 'unchanged')
        self.assertEqual(order.decide(record(), None, self.api(main_status='ahead')), 'first-registration')

    def test_order_directions(self):
        self.assertEqual(order.decide(record(), record(A), self.api()), 'update')
        self.assertEqual(order.decide(record(), record(A), self.api('behind')), 'stale')
        with self.assertRaises(order.OrderError):
            order.decide(record(), record(A), self.api('diverged'))

    def test_not_in_main(self):
        for status in ('behind', 'diverged'):
            with self.subTest(status=status), self.assertRaises(order.OrderError):
                order.decide(record(), None, self.api(main_status=status))

    def test_digest_conflict(self):
        old = record(); old['image_digest'] = 'sha256:'+'e'*64
        with self.assertRaises(order.OrderError):
            order.decide(record(), old, self.api())

    def test_invalid_records(self):
        for old in ({}, [], {'source_sha': B}, {'source_sha': B, 'image_digest': 'bad'}):
            with self.subTest(old=old), self.assertRaises(order.OrderError):
                order.decide(record(), old, self.api())

    def test_invalid_compare(self):
        for value in ({}, None, {'status': 'unknown'},
                      dict(response(B, B, 'identical'), status='unknown'),
                      dict(response(B, B, 'identical'), ahead_by=1)):
            with self.subTest(value=value), self.assertRaises(order.OrderError):
                order.decide(record(), None, lambda p: {'sha': B} if p.endswith('/main') else value)

    def test_api_errors(self):
        for error in (subprocess.CalledProcessError(1, 'gh', stderr='HTTP 403'),
                      subprocess.CalledProcessError(1, 'gh', stderr='HTTP 404'),
                      OSError('network error')):
            with self.subTest(error=error), patch.object(order.subprocess, 'run', side_effect=error):
                with self.assertRaises(order.OrderError):
                    order.decide(record(), None)
        with patch.object(order.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'not json')):
            with self.assertRaises(order.OrderError):
                order.gh_api('test')

    def test_api_error_during_order(self):
        def api(path):
            if path.endswith(f'{A}...{B}'):
                raise order.OrderError('API unavailable')
            return self.api()(path)
        with self.assertRaises(order.OrderError):
            order.decide(record(), record(A), api)

    def test_invalid_main(self):
        for value in ({}, {'sha': 'main'}, None):
            with self.subTest(value=value), self.assertRaises(order.OrderError):
                order.decide(record(), None, lambda p: value)

    def test_cli_missing_vs_broken_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp)/'new.json'; candidate.write_text(json.dumps(record()))
            current = Path(tmp)/'current.json'
            with patch.object(order.sys, 'argv', ['order', '--candidate', str(candidate), '--current', str(current)]), patch.object(order, 'decide', return_value='first-registration') as decide:
                self.assertEqual(order.main(), 0)
                self.assertIsNone(decide.call_args.args[1])
            for content in ('{', '{}', 'null', '[]'):
                current.write_text(content)
                with patch.object(order.sys, 'argv', ['order', '--candidate', str(candidate), '--current', str(current)]), patch.object(order, 'gh_api', side_effect=AssertionError('must fail before API')):
                    self.assertEqual(order.main(), 1)


if __name__ == '__main__':
    unittest.main()
