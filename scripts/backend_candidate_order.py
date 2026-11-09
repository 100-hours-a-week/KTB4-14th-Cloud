"""Order staging candidates using GitHub BASE...HEAD comparisons, never run IDs."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPOSITORY = '100-hours-a-week/KTB4-14th-BE'
SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')


class OrderError(ValueError):
    pass


def gh_api(path):
    # gh reads GH_TOKEN=BE_READ_TOKEN from the existing workflow environment.
    try:
        result = subprocess.run(['gh', 'api', path], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        # Do not echo arbitrary API output or credential-bearing diagnostics.
        raise OrderError('BE history API failed or returned invalid JSON') from exc


def identity(value, label):
    if not isinstance(value, dict):
        raise OrderError(f'{label}: expected JSON object')
    sha, digest = value.get('source_sha'), value.get('image_digest')
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise OrderError(f'{label}: missing or invalid source_sha')
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise OrderError(f'{label}: missing or invalid image_digest')
    return sha, digest


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise OrderError('Cannot read candidate JSON record') from exc


def compare(api, base, head):
    """status describes HEAD relative to BASE; verify counts and ancestry evidence."""
    data = api(f'repos/{REPOSITORY}/compare/{base}...{head}')
    try:
        status = data['status']
        ahead, behind = data['ahead_by'], data['behind_by']
        base_sha, merge_sha = data['base_commit']['sha'], data['merge_base_commit']['sha']
    except (KeyError, TypeError) as exc:
        raise OrderError('Incomplete compare response') from exc
    if type(ahead) is not int or type(behind) is not int or min(ahead, behind) < 0:
        raise OrderError('Invalid compare counts')
    if base_sha != base or not isinstance(merge_sha, str) or not SHA.fullmatch(merge_sha):
        raise OrderError('Invalid compare commit identity')
    valid = {
        'ahead': ahead > 0 and behind == 0 and merge_sha == base and base != head,
        'behind': ahead == 0 and behind > 0 and merge_sha == head and base != head,
        'identical': ahead == behind == 0 and base == head and merge_sha == base,
        'diverged': ahead > 0 and behind > 0 and merge_sha not in (base, head),
    }
    if not valid.get(status, False):
        raise OrderError('Unknown or inconsistent compare status')
    return status


def decide(candidate, current, api=gh_api):
    new_sha, new_digest = identity(candidate, 'new candidate')
    if current is not None:
        old_sha, old_digest = identity(current, 'staging record')
    main = api(f'repos/{REPOSITORY}/commits/main')
    main_sha = main.get('sha') if isinstance(main, dict) else None
    if not isinstance(main_sha, str) or not SHA.fullmatch(main_sha):
        raise OrderError('Invalid BE main response')
    # BASE=candidate, HEAD=resolved main: ahead/identical proves candidate is on main.
    if compare(api, new_sha, main_sha) not in ('ahead', 'identical'):
        raise OrderError('Candidate is not in BE main history')
    if current is None:
        return 'first-registration'  # staging tracking only; not a production baseline
    if old_sha == new_sha:
        if old_digest != new_digest:
            raise OrderError('Same source SHA has a different digest')
        return 'unchanged'
    # BASE=existing staging SHA, HEAD=new candidate SHA.
    status = compare(api, old_sha, new_sha)
    if status == 'ahead':
        return 'update'
    if status == 'behind':
        return 'stale'
    raise OrderError('Staging and candidate histories diverge or cannot be ordered')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--current', required=True)
    args = parser.parse_args()
    try:
        path = Path(args.current)
        # A broken symlink/directory/unreadable or malformed record must fail closed.
        current = None
        if path.exists() or path.is_symlink():
            current = read_json(path)
            identity(current, 'staging record')
        print(decide(read_json(args.candidate), current))
    except OrderError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
