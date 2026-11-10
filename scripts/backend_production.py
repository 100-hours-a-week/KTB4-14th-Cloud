"""Manual production promotion. Preview is read-only; only seven image fields may change."""
import argparse
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

import backend_staging as staging
import backend_staging_values as values

GROUPS = {'auth': ('auth-api',), 'chat': ('chat-api', 'chat-ws'),
          'travel': ('travel-api', 'travel-worker'), 'general': ('general-api', 'general-ws')}
TARGETS = tuple(t for group in GROUPS.values() for t in group)
BASELINE = 'deploy/backend-production-baseline.json'
CLOUD_REPO = '100-hours-a-week/KTB4-14th-Cloud'


def require(ok, message):
    values.require(ok, message)


def command(*args, cwd=None, env=None):
    return subprocess.check_output(args, cwd=cwd, env=env, stderr=subprocess.PIPE).decode()


def classify(path):
    # Cross-domain callers are conservatively grouped; see mapping evidence document.
    if path.startswith(('docs/', 'src/test/')) or path.endswith('.md') and '/' not in path:
        return set()
    prefix = 'src/main/java/com/audigo/domain/'
    if path.startswith(prefix):
        domain = path[len(prefix):].split('/')[0]
        if domain in ('auth', 'user'):
            return set(TARGETS)  # auth/current-user and user entities shared throughout domains
        if domain == 'chat':
            return set(GROUPS['chat'] + GROUPS['general'])
        if domain == 'travel':
            return set(GROUPS['travel'] + GROUPS['general'])
        if domain in ('matching', 'notification'):
            return set(TARGETS)  # cross-domain event producers and persistence dependencies
    return set(TARGETS)  # Unknown includes build/runtime/config/workflow changes: fail conservatively.


def changed_paths(be, old, new):
    for sha in (old, new):
        require(re.fullmatch('[0-9a-f]{40}', sha), 'Invalid comparison SHA')
        require(subprocess.run(['git', 'cat-file', '-e', sha + '^{commit}'], cwd=be,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0,
                'Comparison commit missing from full history')
    require(subprocess.run(['git', 'merge-base', '--is-ancestor', old, new], cwd=be,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0,
            'Candidate precedes or diverges from production baseline')
    # No compare API pagination/truncation. Rename sides both occur with --no-renames.
    raw = subprocess.check_output(['git', 'diff', '--no-renames', '--name-only', '-z', old, new, '--'], cwd=be)
    return raw.decode('utf-8').strip('\0').split('\0') if raw else []


def production_baseline(root):
    path = root / BASELINE
    require(path.is_file() and not path.is_symlink(), 'Production baseline missing or not a regular file')
    baseline = values.load_json(path.read_text())
    require(isinstance(baseline, dict) and set(baseline) == {'schema_version', 'deployments'}
            and type(baseline['schema_version']) is int and baseline['schema_version'] == 1
            and isinstance(baseline['deployments'], dict)
            and set(baseline['deployments']) == set(TARGETS), 'Missing or invalid production baselines')
    for target in TARGETS:
        entry = baseline['deployments'][target]
        require(isinstance(entry, dict) and set(entry) == {'source_sha', 'image_digest', 'confirmed_by', 'confirmed_at', 'evidence'},
                'Invalid baseline entry: ' + target)
        require(all(isinstance(v, str) and v.strip() for v in entry.values()), 'Unconfirmed baseline: ' + target)
        require(re.fullmatch('[0-9a-f]{40}', entry['source_sha'])
                and re.fullmatch('sha256:[0-9a-f]{64}', entry['image_digest']), 'Invalid baseline SHA/digest: ' + target)
        confirmed = datetime.fromisoformat(entry['confirmed_at'].replace('Z', '+00:00'))
        require(confirmed.tzinfo is not None, 'Baseline confirmation timestamp needs timezone')
    return baseline


def plan(root, be, candidate, expected_digest, evidence):
    values.validate_candidate(candidate)
    require(candidate['image_digest'] == expected_digest, 'Requested digest does not match validated artifact')
    if evidence is not None:
        require(isinstance(evidence, str) and 20 <= len(evidence.strip()) <= 8000,
                'Provide human staging evidence: both deployments, digest, time, checks and verifier')
    require(values.validate_candidate(values.load_json((root / values.RECORD).read_text())) == candidate,
            'Cloud main staging candidate is different from requested candidate')
    for name in values.VALUES:
        doc, _ = values.parse_yaml((root / name).read_text())
        require(doc['image'].get('tag') == candidate['image_tag']
                and doc['image']['digest'] == expected_digest, 'Staging values differ from candidate')
    baseline = production_baseline(root)
    rows = {}
    for target in TARGETS:
        entry = baseline['deployments'][target]
        name = f'apps/spring-value/{target}-prod.yaml'
        doc, _ = values.parse_yaml((root / name).read_text())
        require(doc['image']['digest'] == entry['image_digest']
                and ('tag' not in doc['image'] or doc['image']['tag'] == 'sha-' + entry['source_sha']),
                'Pending deployment or Git/actual baseline mismatch: ' + target)
        paths = changed_paths(be, entry['source_sha'], candidate['source_sha'])
        reasons = [p for p in paths if target in classify(p)]
        rows[target] = dict(target=target, file=name, previous_sha=entry['source_sha'],
                            previous_digest=entry['image_digest'], paths=reasons)
    selected = {t for t, r in rows.items() if r['paths']}
    for group in GROUPS.values():
        if selected.intersection(group):
            selected.update(group)
    result = []
    for t in TARGETS:
        if t in selected:
            row = rows[t]
            if not row['paths']:
                row['paths'] = ['paired rollout: ' + ', '.join(member for group in GROUPS.values() if t in group for member in group)]
            row.update(candidate_sha=candidate['source_sha'], candidate_digest=expected_digest)
            result.append(row)
    return result


def verify_changes(root, rows, candidate):
    allowed = {r['file'] for r in rows}
    # Reject non-values, untracked files, deletions, symlinks, staged changes and mode changes.
    require(not values.git(root, 'diff', '--cached', '--name-only').strip(), 'Index must remain empty before verification')
    for entry in filter(None, values.git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignored=matching', '--no-renames').decode().split('\0')):
        require(entry[:2] == ' M' and entry[3:] in allowed, 'Non-allowlisted file/status changed')
    for row in rows:
        name = row['file']; p = root / name
        require(p.is_file() and not p.is_symlink(), 'Values replaced')
        mode = values.git(root, 'ls-tree', 'HEAD', '--', name).decode().split()[0]
        require(bool(p.stat().st_mode & 0o111) == (mode == '100755'), 'Values mode changed')
        values.compare_values(values.baseline(root, name), p.read_text(), candidate)
    values.git(root, 'diff', '--check')


def update(root, rows, candidate):
    require(not values.git(root, 'status', '--porcelain=v1', '--untracked-files=all', '--ignored=matching').strip(),
            'Production updater requires clean checkout')
    originals = {r['file']: (root / r['file']).read_text() for r in rows}
    try:
        for name, text in originals.items():
            doc, _ = values.parse_yaml(text)
            doc['image']['tag'] = candidate['image_tag']; doc['image']['digest'] = candidate['image_digest']
            buf = io.StringIO(); values.yaml_tool().dump(doc, buf)
            values.compare_values(text, buf.getvalue(), candidate)
            (root / name).write_text(buf.getvalue())
        verify_changes(root, rows, candidate)
    except BaseException:
        for name, text in originals.items():
            (root / name).write_text(text)
        raise


def pr_body(rows, candidate, evidence):
    lines = ['## 검증한 BE 이미지 운영 승격', '', '### 스테이징 검증 근거 (사람 확인)', evidence,
             '', f"후보: `{candidate['source_sha']}` / `{candidate['image_digest']}`",
             f"빌드: {candidate['workflow_run_url']}", '',
             '| 대상 | 이전 SHA | 롤백 digest | 변경 근거 |', '|---|---|---|---|']
    for r in rows:
        lines.append(f"| {r['target']} | {r['previous_sha']} | {r['previous_digest']} | " + ', '.join(r['paths']).replace('|', '\\|').replace('\n', ' ') + ' |')
    lines += ['', '### 수동 배포 및 확인',
              '- 병합은 Git 예정 버전 갱신입니다. Cloud 팀이 해당 Application을 수동 Sync합니다.',
              '- 대상별 실제 Pod digest, Ready, DB·Redis 연결과 해당 기능을 확인합니다.',
              '- 확인 후 별도 PR로 실제 배포 SHA·digest 및 확인자·시각·근거를 기준선에 기록합니다.',
              '- 실패 시 표의 이전 SHA 태그·롤백 digest로 values를 되돌리고 수동 Sync합니다.',
              '- 이 PR은 실제 배포 완료 기준선을 변경하지 않습니다.']
    if any(r['target'] in GROUPS['travel'] for r in rows):
        lines += ['- 여행 SQS·Worker 경로는 현재 스테이징에서 미검증입니다. 운영 승인 시 이 제한을 확인합니다.',
                  '- 운영 Sync 후 generation_job_id로 요청 발행·AI 소비·결과 소비·완료를 연결해 확인합니다.',
                  '- ECS 및 다른 여행 Pod와의 동시 소비, 중복 처리 및 재시도 영향도 확인합니다.']
    body = '\n'.join(lines) + '\n'
    require(len(body.encode()) < 60000, 'Preview too large for PR; inspect and reduce candidate range')
    return body


def cloud_gh(*args):
    token = os.environ.get('CLOUD_PR_TOKEN')
    require(bool(token), 'Separate Cloud PR write token is required')
    return command('gh', *args, env=dict(os.environ, GH_TOKEN=token))


def no_open_promotion():
    pages = json.loads(cloud_gh('api', '--paginate', '--slurp',
        'repos/100-hours-a-week/KTB4-14th-Cloud/pulls?state=open&base=main&per_page=100'))
    require(not any(p['head']['ref'].startswith('backend-production/')
                    or p['title'].startswith('[Deploy] BE 운영 승격') for page in pages for p in page),
            'An open production promotion PR exists')


def staging_result(run_id, attempt):
    require(staging.positive(run_id) and staging.positive(attempt), 'Invalid Cloud run ID or attempt')
    env = dict(os.environ, GH_TOKEN=os.environ['CLOUD_READ_TOKEN'])
    base = f'repos/{CLOUD_REPO}/actions'
    pages = json.loads(command('gh', 'api', '--paginate', '--slurp',
                               f'{base}/runs/{run_id}/artifacts?per_page=100', env=env))
    name = f'staging-backend-result-{run_id}-{attempt}'
    matches = [a for page in pages for a in page['artifacts'] if a['name'] == name]
    if not matches:
        return None
    require(len(matches) == 1 and matches[0]['expired'] is False
            and type(matches[0]['id']) is int and 0 < matches[0]['size_in_bytes'] <= 65536,
            'Invalid staging result artifact')
    raw = subprocess.check_output(['gh', 'api', f"{base}/artifacts/{matches[0]['id']}/zip"], env=env, stderr=subprocess.PIPE)
    require(len(raw) <= 65536, 'Staging result artifact too large')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) == 1 and entries[0].filename == 'staging-result.json'
                and entries[0].file_size <= 16384, 'Invalid staging result archive')
        return values.load_json(archive.read(entries[0]).decode('utf-8'))


def current_staging_candidate():
    env = dict(os.environ, GH_TOKEN=os.environ['CLOUD_READ_TOKEN'])
    raw = command('gh', 'api', '-H', 'Accept: application/vnd.github.raw+json',
                  f'repos/{CLOUD_REPO}/contents/{values.RECORD}?ref=main', env=env)
    return values.validate_candidate(values.load_json(raw))


def require_approval_environment():
    env = dict(os.environ, GH_TOKEN=os.environ['CLOUD_READ_TOKEN'])
    data = json.loads(command('gh', 'api', f'repos/{CLOUD_REPO}/environments/backend-production', env=env))
    require(any(rule.get('type') == 'required_reviewers' and rule.get('reviewers')
                for rule in data.get('protection_rules', [])),
            'backend-production environment must require an assigned reviewer')


def validate_staging_result(root, result):
    require(isinstance(result, dict) and result.get('status') == 'published', 'Staging did not publish')
    commit = result.get('cloud_commit')
    require(isinstance(commit, str) and re.fullmatch('[0-9a-f]{40}', commit), 'Invalid Cloud commit')
    command('git', 'merge-base', '--is-ancestor', commit, 'HEAD', cwd=root)
    candidate = values.validate_candidate(values.load_json(command('git', 'show', f'{commit}:{values.RECORD}', cwd=root)))
    for key in ('workflow_run_id', 'workflow_run_attempt', 'source_sha', 'image_digest'):
        require(result.get(key) == candidate[key], 'Artifact and Cloud commit candidate differ')
    require(values.validate_candidate(values.load_json((root / values.RECORD).read_text())) == candidate,
            'Cloud main staging candidate has changed')
    for name in values.VALUES:
        doc, _ = values.parse_yaml(command('git', 'show', f'{commit}:{name}', cwd=root))
        require(doc['image'].get('tag') == candidate['image_tag'] and doc['image']['digest'] == candidate['image_digest'],
                'Published commit staging values differ from candidate')
    return candidate


def auto_preview(root, be):
    event = values.load_json(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    run = event['workflow_run']
    require(os.environ.get('GITHUB_EVENT_NAME') == 'workflow_run'
            and os.environ.get('GITHUB_REPOSITORY') == CLOUD_REPO
            and run.get('name') in ('Update staging backend image', 'BE 스테이징 이미지 갱신')
            and run.get('path') == '.github/workflows/backend-staging.yml'
            and run.get('head_branch') == 'main' and run.get('status') == 'completed'
            and run.get('conclusion') == 'success'
            and run.get('repository', {}).get('full_name') == CLOUD_REPO
            and run.get('head_repository', {}).get('full_name') == CLOUD_REPO,
            'Untrusted staging workflow run')
    run_id, attempt = str(run['id']), str(run['run_attempt'])
    result = staging_result(run_id, attempt)
    if result is None:
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write('스테이징 갱신 결과 artifact가 없어 운영 미리보기를 건너뜁니다.\n')
        return
    recorded = validate_staging_result(root, result)
    candidate = staging.execute(root, recorded['workflow_run_id'], recorded['workflow_run_attempt'],
                                staging.Client(), validate_only=True)
    require(candidate == recorded, 'BE artifact differs from published staging candidate')
    command('git', 'merge-base', '--is-ancestor', candidate['source_sha'], 'origin/main', cwd=be)
    rows = plan(root, be, candidate, candidate['image_digest'], None)
    latest = current_staging_candidate()
    require(latest == candidate, 'Cloud main staging candidate changed during preview')
    if rows:
        require_approval_environment()
    lines = ['## 운영 승격 미리보기', '', '스테이징 배포 및 기능 검증은 아직 확인되지 않았습니다.',
             f"후보: `{candidate['source_sha']}` / `{candidate['image_digest']}`", '',
             '| 대상 | 변경 근거 |', '|---|---|']
    for row in rows:
        lines.append(f"| {row['target']} | " + ', '.join(row['paths']).replace('|', '\\|').replace('\n', ' ') + ' |')
    if not rows:
        lines.append('| 없음 | 운영 대상 변경 없음 |')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
        stream.write('\n'.join(lines) + '\n')
    return candidate, rows


def plan_fingerprint(root, candidate, rows):
    names = [BASELINE, values.RECORD, *values.VALUES,
             *(f'apps/spring-value/{target}-prod.yaml' for target in TARGETS)]
    files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}
    data = json.dumps({'candidate': candidate, 'rows': rows, 'files': files},
                      sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(data.encode()).hexdigest()


def create_promotion_pr(root, rows, candidate, evidence):
    no_open_promotion()
    update(root, rows, candidate)
    remote = cloud_gh('api', f'repos/{CLOUD_REPO}/git/ref/heads/main')
    require(json.loads(remote)['object']['sha'] == values.git(root, 'rev-parse', 'HEAD').decode().strip(),
            'Cloud main moved; rerun preview and promotion')
    run_id = os.environ.get('GITHUB_RUN_ID', 'manual')
    attempt = os.environ.get('GITHUB_RUN_ATTEMPT', '1')
    require(staging.positive(run_id) and staging.positive(attempt), 'Invalid promotion run ID')
    branch = f'backend-production/{run_id}-{attempt}'
    command('git', 'switch', '-c', branch, cwd=root)
    command('git', 'config', 'user.name', 'github-actions[bot]', cwd=root)
    command('git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com', cwd=root)
    command('git', 'add', '--', *(r['file'] for r in rows), cwd=root)
    command('git', 'commit', '-m', '배포: 검증한 BE 이미지 운영 승격 준비', cwd=root)
    command('git', 'push', 'origin', f'HEAD:refs/heads/{branch}', cwd=root)
    no_open_promotion()
    return cloud_gh('pr', 'create', '--repo', CLOUD_REPO, '--base', 'main', '--head', branch,
                    '--title', '[Deploy] BE 운영 승격 ' + candidate['source_sha'][:12],
                    '--body', pr_body(rows, candidate, evidence)).strip()


def request_auto_merge(pr_url):
    # Protection API visibility is mandatory; uncertainty leaves the PR for review.
    try:
        repo = json.loads(cloud_gh('api', f'repos/{CLOUD_REPO}'))
        protection = json.loads(cloud_gh('api', f'repos/{CLOUD_REPO}/branches/main/protection'))
        checks = protection.get('required_status_checks') or {}
        reviews = protection.get('required_pull_request_reviews') or {}
        require(repo.get('allow_auto_merge') is True
                and bool(checks.get('contexts') or checks.get('checks'))
                and reviews.get('required_approving_review_count', 0) >= 1,
                'Auto-merge or required checks/reviews are not configured')
        cloud_gh('pr', 'merge', '--repo', CLOUD_REPO, '--auto', '--merge', pr_url)
        return '필수 검사와 리뷰를 기다리는 자동 병합을 요청했습니다.'
    except Exception:
        return '자동 병합 설정·보호 규칙을 확인하지 못했거나 요청이 거절되어 리뷰 대기로 남겼습니다.'


def auto_approved(root, be, expected):
    require(re.fullmatch('[0-9a-f]{64}', expected or ''), 'Approved preview fingerprint missing')
    require(not values.git(root, 'status', '--porcelain=v1', '--untracked-files=all', '--ignored=matching').strip(),
            'Expected clean Cloud checkout')
    result = auto_preview(root, be)
    require(result is not None, 'Published staging result is missing')
    candidate, rows = result
    require(bool(rows) and plan_fingerprint(root, candidate, rows) == expected,
            'Candidate, baseline, values or deployment plan changed after approval')
    link = f"https://github.com/{CLOUD_REPO}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    evidence = ('담당자가 스테이징 기능 검증 후 GitHub Environment 승인을 진행하는 절차입니다. '
                '구체적인 기능 검증 결과는 담당자가 별도 기록해야 합니다. 승인 실행: ' + link)
    url = create_promotion_pr(root, rows, candidate, evidence)
    require(re.fullmatch(r'https://github\.com/100-hours-a-week/KTB4-14th-Cloud/pull/[1-9][0-9]*', url),
            'Unexpected PR URL')
    message = request_auto_merge(url)
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
        stream.write(f'\n운영 승격 PR: {url}\n\n{message}\n')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--be', required=True)
    p.add_argument('--mode', choices=('preview', 'pr', 'auto-preview', 'auto-approved'), default='preview')
    args = p.parse_args()
    root = Path.cwd(); be = Path(args.be).resolve()
    try:
        if args.mode == 'auto-preview':
            require(os.environ.get('GITHUB_ACTIONS') == 'true', 'GitHub Actions required')
            result = auto_preview(root, be)
            with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
                if result and result[1]:
                    stream.write('has_targets=true\n')
                    stream.write('fingerprint=' + plan_fingerprint(root, *result) + '\n')
                else:
                    stream.write('has_targets=false\n')
            return 0
        if args.mode == 'auto-approved':
            require(os.environ.get('GITHUB_ACTIONS') == 'true', 'GitHub Actions required')
            auto_approved(root, be, os.environ.get('APPROVED_FINGERPRINT', ''))
            return 0
        require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_REF') == 'refs/heads/main',
                'Dispatch the trusted workflow from main')
        c = staging.execute(root, os.environ.get('RUN_ID', ''), os.environ.get('RUN_ATTEMPT', ''), staging.Client(), validate_only=True)
        require(command('git', 'branch', '--show-current', cwd=root).strip() == 'main', 'Expected Cloud main checkout')
        require(not values.git(root, 'status', '--porcelain=v1', '--untracked-files=all', '--ignored=matching').strip(), 'Expected clean Cloud checkout')
        command('git', 'merge-base', '--is-ancestor', c['source_sha'], 'origin/main', cwd=be)
        require(os.environ.get('STAGING_DEPLOYMENTS_CONFIRMED') == 'true', 'Human must confirm both staging deployments at candidate digest')
        evidence = os.environ.get('STAGING_EVIDENCE', '')
        rows = plan(root, be, c, os.environ.get('CANDIDATE_DIGEST', ''), evidence)
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        body = pr_body(rows, c, evidence)
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write(body)
        if args.mode == 'preview' or not rows:
            return 0
        require_approval_environment()
        print(create_promotion_pr(root, rows, c, evidence))
        return 0
    except Exception as exc:
        if isinstance(exc, (values.ValidationError, staging.IntegrationError)):
            print(str(exc), file=sys.stderr)
        print('Production promotion blocked. Review candidate, baseline, history and permissions; no deployment is confirmed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
