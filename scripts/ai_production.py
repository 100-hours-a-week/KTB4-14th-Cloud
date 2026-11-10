"""Preview and propose a verified staging AI image for the three production AI workloads.

The production baseline is intentionally a human-maintained record: creating a PR does
not assert that Argo CD has synced or that the resulting Pods are healthy.
"""
import argparse
import hashlib
import io
import json
import os
from datetime import datetime
from pathlib import Path
import re
import subprocess
import sys
import zipfile

import ai_staging as staging

CLOUD_REPO = '100-hours-a-week/KTB4-14th-Cloud'
STAGING_WORKFLOW = '.github/workflows/ai-staging.yml'
STAGING_WORKFLOW_NAME = 'AI 스테이징 이미지 갱신'
STAGING_WORKFLOW_NAMES = frozenset((STAGING_WORKFLOW_NAME, 'Update staging AI image'))
RECORD = 'apps/ai-values/staging-ai-candidate.json'
STAGING_VALUES = 'apps/ai-values/values-ai-staging.yaml'
BASELINE = 'deploy/ai-production-baseline.json'
TARGETS = {
    'match-ai': 'apps/ai-values/values-ai-match-prod.yaml',
    'travel-ai': 'apps/ai-values/values-ai-travel-prod.yaml',
    'travel-ai-worker': 'apps/ai-values/values-ai-travel-worker-prod.yaml',
}
SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')


def require(ok, message):
    if not ok:
        raise staging.Error(message)


def command(*args, cwd=None, env=None, binary=False):
    result = subprocess.run(args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    require(result.returncode == 0, 'Command failed: ' + args[0])
    return result.stdout if binary else result.stdout.decode()


def cloud_env(write=False):
    name = 'CLOUD_PR_TOKEN' if write else 'CLOUD_READ_TOKEN'
    token = os.environ.get(name, '')
    require(bool(token), name + ' is required')
    return dict(os.environ, GH_TOKEN=token)


def cloud_api(path, write=False, binary=False):
    return command('gh', 'api', path, env=cloud_env(write), binary=binary)


def append_summary(text):
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as stream:
            stream.write(text.rstrip() + '\n')


def image_doc(path):
    text = path.read_text()
    doc = staging.yaml_tool().load(text)
    require(isinstance(doc, dict) and isinstance(doc.get('image'), dict), 'Invalid image values: ' + str(path))
    repo, digest = doc['image'].get('repository'), doc['image'].get('digest')
    require(isinstance(repo, str) and isinstance(digest, str) and DIGEST.fullmatch(digest),
            'Invalid image values: ' + str(path))
    return text, doc


def production_baseline(root):
    path = root / BASELINE
    if not path.is_file() or path.is_symlink():
        return None
    data = staging.load_json(path.read_text())
    require(isinstance(data, dict) and set(data) == {'schema_version', 'deployments'}
            and data['schema_version'] == 1 and isinstance(data['deployments'], dict)
            and set(data['deployments']) == set(TARGETS), 'Invalid AI production baseline schema')
    for target, entry in data['deployments'].items():
        require(isinstance(entry, dict) and set(entry) == {'source_sha', 'image_digest', 'confirmed_by', 'confirmed_at', 'evidence'},
                'Invalid AI production baseline entry: ' + target)
        require(all(isinstance(v, str) and v.strip() for v in entry.values())
                and SHA.fullmatch(entry['source_sha']) and DIGEST.fullmatch(entry['image_digest']),
                'Unconfirmed AI production baseline: ' + target)
        try:
            require(datetime.fromisoformat(entry['confirmed_at'].replace('Z', '+00:00')).tzinfo is not None,
                    'AI baseline confirmation time needs timezone')
        except ValueError as exc:
            raise staging.Error('Invalid AI baseline confirmation time: ' + target) from exc
    return data


def require_baseline(root, candidate):
    baseline = production_baseline(root)
    if baseline is None:
        current = []
        for target, name in TARGETS.items():
            _, doc = image_doc(root / name)
            current.append(f'| {target} | Git 예정 digest (실제 배포 미확인) | `{doc["image"]["digest"]}` | `{candidate["image_digest"]}` |')
        append_summary('''## AI 운영 승격 미리보기 중단

후보 SHA: `''' + candidate['source_sha'] + '`\n후보 digest: `' + candidate['image_digest'] + '''`

| 대상 | 이전 값 | 현재 Git digest | 후보 digest |
|---|---|---|---|
''' + '\n'.join(current) + '''

실제 운영 기준선이 없습니다. `deploy/ai-production-baseline.json`을 별도 리뷰로 등록한 뒤 다시 실행하세요.

각 `match-ai`, `travel-ai`, `travel-ai-worker`에는 실제 Pod/Application에서 확인한 `source_sha`, `image_digest`, `confirmed_by`, UTC ISO-8601 `confirmed_at`, 확인 근거(`evidence`)가 필요합니다. 기능 테스트·Argo CD Sync 미수행 항목을 통과로 기록하면 안 됩니다.''')
        return None
    return baseline


def result_artifact(run_id, attempt):
    require(staging.POSITIVE.fullmatch(run_id or '') and staging.POSITIVE.fullmatch(attempt or ''),
            'Invalid Cloud staging run ID or attempt')
    api = f'repos/{CLOUD_REPO}/actions'
    raw = cloud_api(f'{api}/runs/{run_id}/artifacts?per_page=100')
    page = staging.load_json(raw)
    require(isinstance(page.get('artifacts'), list) and page.get('total_count') == len(page['artifacts']),
            'Incomplete staging artifact list')
    name = f'staging-ai-result-{run_id}-{attempt}'
    artifacts = [a for a in page['artifacts'] if isinstance(a, dict) and a.get('name') == name]
    if not artifacts:
        return None
    require(len(artifacts) == 1 and artifacts[0].get('expired') is False
            and isinstance(artifacts[0].get('id'), int) and 0 < artifacts[0].get('size_in_bytes', 0) <= 65536,
            'Invalid staging result artifact')
    data = cloud_api(f"{api}/artifacts/{artifacts[0]['id']}/zip", binary=True)
    require(len(data) <= 65536, 'Staging result artifact too large')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            require(len(entries) == 1 and entries[0].filename == 'ai-staging-result.json'
                    and not entries[0].is_dir() and entries[0].file_size <= 16384,
                    'Invalid staging result archive')
            return staging.load_json(archive.read(entries[0]).decode('utf-8'))
    except (zipfile.BadZipFile, UnicodeError) as exc:
        raise staging.Error('Invalid staging result archive') from exc


def trusted_staging_run(run_id, attempt):
    workflow = staging.load_json(cloud_api(f'repos/{CLOUD_REPO}/actions/workflows/ai-staging.yml'))
    run = staging.load_json(cloud_api(f'repos/{CLOUD_REPO}/actions/runs/{run_id}/attempts/{attempt}'))
    require(workflow.get('path') == STAGING_WORKFLOW and isinstance(workflow.get('id'), int)
            and run.get('workflow_id') == workflow['id'] and run.get('path') == STAGING_WORKFLOW
            and run.get('name') in STAGING_WORKFLOW_NAMES and str(run.get('id')) == run_id
            and str(run.get('run_attempt')) == attempt and run.get('event') == 'workflow_dispatch'
            and run.get('head_branch') == 'main' and run.get('status') == 'completed'
            and run.get('conclusion') == 'success'
            and run.get('repository', {}).get('full_name') == CLOUD_REPO
            and run.get('head_repository', {}).get('full_name') == CLOUD_REPO,
            'Run is not the designated successful Cloud main AI staging execution')
    return run


def validate_result(root, run_id, attempt):
    trusted_staging_run(run_id, attempt)
    result = result_artifact(run_id, attempt)
    if result is None:
        append_summary('스테이징 갱신 결과 artifact가 없어 운영 미리보기를 건너뜁니다.')
        return None
    require(isinstance(result, dict) and result.get('status') == 'published'
            and isinstance(result.get('cloud_commit'), str) and SHA.fullmatch(result['cloud_commit'])
            and isinstance(result.get('candidate'), dict), 'Invalid staging result data')
    candidate = staging.validate(result['candidate'], staging.repository(root))
    for field in ('source_sha', 'image_digest', 'workflow_run_id', 'workflow_run_attempt'):
        require(result.get(field) == candidate[field], 'Staging result candidate mismatch')
    commit = result['cloud_commit']
    command('git', 'merge-base', '--is-ancestor', commit, 'HEAD', cwd=root)
    at_commit = staging.validate(staging.load_json(command('git', 'show', f'{commit}:{RECORD}', cwd=root)), staging.repository(root))
    require(at_commit == candidate, 'Staging artifact and recorded Cloud commit differ')
    committed_values = command('git', 'show', f'{commit}:{STAGING_VALUES}', cwd=root)
    committed_doc = staging.yaml_tool().load(committed_values)
    require(isinstance(committed_doc, dict) and isinstance(committed_doc.get('image'), dict)
            and committed_doc['image'].get('repository') == candidate['image_repository']
            and committed_doc['image'].get('digest') == candidate['image_digest'],
            'Staging artifact and published Cloud values differ')
    _, doc = image_doc(root / STAGING_VALUES)
    require(doc['image']['repository'] == candidate['image_repository'] and doc['image']['digest'] == candidate['image_digest'],
            'Current Cloud main staging values changed')
    require(staging.validate(staging.load_json((root / RECORD).read_text()), staging.repository(root)) == candidate,
            'Current Cloud main staging candidate changed')
    fetched = staging.fetch_candidate(root, candidate['workflow_run_id'], candidate['workflow_run_attempt'], staging.Client())
    require(fetched == candidate, 'AI source artifact differs from staging result')
    staging.validate_ecr(staging.Client(), candidate)
    return candidate


def plan(root, candidate, baseline):
    rows = []
    for target, name in TARGETS.items():
        text, doc = image_doc(root / name)
        before = baseline['deployments'][target]
        require(doc['image']['repository'] == candidate['image_repository'], 'Production image repository differs: ' + target)
        require(doc['image']['digest'] == before['image_digest'],
                'Previous production Sync or baseline update remains: ' + target)
        if before['image_digest'] != candidate['image_digest']:
            rows.append({'target': target, 'file': name, 'previous_sha': before['source_sha'],
                         'previous_digest': before['image_digest'], 'candidate_digest': candidate['image_digest'],
                         'text': text})
    return rows


def fingerprint(root, candidate, rows):
    files = [BASELINE, RECORD, STAGING_VALUES, *TARGETS.values()]
    data = {'candidate': candidate, 'rows': [{k: v for k, v in r.items() if k != 'text'} for r in rows],
            'files': {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in files}}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def check_environment():
    env = staging.load_json(cloud_api(f'repos/{CLOUD_REPO}/environments/ai-production'))
    require(any(rule.get('type') == 'required_reviewers' and rule.get('reviewers')
                for rule in env.get('protection_rules', [])),
            'ai-production environment must require an assigned reviewer')


def preview(root, run_id, attempt):
    candidate = validate_result(root, run_id, attempt)
    if candidate is None:
        return None, []
    baseline = require_baseline(root, candidate)
    if baseline is None:
        return candidate, []
    rows = plan(root, candidate, baseline)
    check_environment()
    append_summary('\n'.join(['## AI 운영 승격 미리보기', '',
        f"후보 SHA: `{candidate['source_sha']}`", f"후보 digest: `{candidate['image_digest']}`", '',
        '| 대상 | 이전 digest | 후보 digest |', '|---|---|---|',
        *[f"| {r['target']} | `{r['previous_digest']}` | `{r['candidate_digest']}` |" for r in rows], '',
        '스테이징 여행 생성/매칭 API 기능 검증은 이 자동 미리보기에서 완료로 기록하지 않습니다. 담당자가 확인한 뒤 Environment를 승인해야 합니다.']))
    return candidate, rows


def no_open_pr():
    data = staging.load_json(command('gh', 'api', '--paginate', '--slurp',
        f'repos/{CLOUD_REPO}/pulls?state=open&base=main&per_page=100', env=cloud_env(True)))
    require(not any(pr.get('head', {}).get('ref', '').startswith('ai-production/')
                    or pr.get('title', '').startswith('[Deploy] AI Kubernetes 운영 승격')
                    for page in data for pr in page), 'An open AI production promotion PR exists')


def replace_digest(text, old, new):
    pattern = re.compile(r'(?m)^(\s*digest:\s*["\']?)' + re.escape(old) + r'(["\']?\s*(?:#.*)?)$')
    changed, count = pattern.subn(r'\g<1>' + new + r'\g<2>', text, count=1)
    require(count == 1, 'Expected exactly one image digest field')
    return changed


def create_pr(root, candidate, rows):
    no_open_pr()
    require(not command('git', 'status', '--porcelain=v1', cwd=root).strip(), 'Expected a clean Cloud checkout')
    for row in rows:
        updated = replace_digest(row['text'], row['previous_digest'], candidate['image_digest'])
        (root / row['file']).write_text(updated)
        require((root / row['file']).read_text() == updated, 'Production values write failed')
    changed = command('git', 'diff', '--name-only', cwd=root).splitlines()
    require(set(changed) == {r['file'] for r in rows}, 'Only production AI image digests may change')
    command('git', 'diff', '--check', cwd=root)
    remote = staging.load_json(command('gh', 'api', f'repos/{CLOUD_REPO}/git/ref/heads/main', env=cloud_env(True)))
    require(remote.get('object', {}).get('sha') == command('git', 'rev-parse', 'HEAD', cwd=root).strip(),
            'Cloud main moved; rerun preview')
    run_id, attempt = os.environ.get('GITHUB_RUN_ID', ''), os.environ.get('GITHUB_RUN_ATTEMPT', '')
    require(staging.POSITIVE.fullmatch(run_id) and staging.POSITIVE.fullmatch(attempt), 'Invalid promotion run identity')
    branch = f'ai-production/{run_id}-{attempt}'
    command('git', 'switch', '-c', branch, cwd=root)
    command('git', 'config', 'user.name', 'github-actions[bot]', cwd=root)
    command('git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com', cwd=root)
    command('git', 'add', '--', *(r['file'] for r in rows), cwd=root)
    command('git', 'commit', '-m', '배포: 검증한 AI 이미지 운영 승격 준비', cwd=root)
    command('git', 'push', 'origin', f'HEAD:refs/heads/{branch}', cwd=root)
    no_open_pr()
    body = '\n'.join(['## 검증 후보 AI Kubernetes 운영 승격', '',
        f"후보: `{candidate['source_sha']}` / `{candidate['image_digest']}`", '',
        '| 대상 | 이전 SHA | 롤백 digest |', '|---|---|---|',
        *[f"| {r['target']} | {r['previous_sha']} | {r['previous_digest']} |" for r in rows], '',
        '### 배포 전제 및 확인',
        '- 이 PR은 수동 병합만 합니다. 병합 뒤 Cloud 팀이 각 Argo CD Application을 수동 Sync합니다.',
        '- 스테이징 여행 생성·매칭 API 검증은 담당자가 별도로 완료·기록해야 하며, 이 PR은 자동 통과로 취급하지 않습니다.',
        '- 운영 `travel-ai-worker`는 Sync 후 SQS 작업 큐 소비와 결과 큐/저장소 반영을 확인해야 합니다.',
        '- 각 Pod Ready와 실제 digest를 확인한 뒤 별도 기준선 PR로 확인자·시각·근거를 갱신합니다.',
        '- 실패 시 표의 롤백 digest로 values를 되돌린 뒤 수동 Sync합니다.']) + '\n'
    url = command('gh', 'pr', 'create', '--repo', CLOUD_REPO, '--base', 'main', '--head', branch,
                  '--title', '[Deploy] AI Kubernetes 운영 승격 ' + candidate['source_sha'][:12], '--body', body,
                  env=cloud_env(True)).strip()
    append_summary('운영 승격 PR (수동 병합): ' + url)


def ids_for_mode(mode):
    if mode == 'auto-preview':
        event = staging.load_json(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        run = event.get('workflow_run', {})
        require(os.environ.get('GITHUB_EVENT_NAME') == 'workflow_run' and os.environ.get('GITHUB_REPOSITORY') == CLOUD_REPO
                and run.get('name') in STAGING_WORKFLOW_NAMES and run.get('path') == STAGING_WORKFLOW
                and run.get('head_branch') == 'main' and run.get('conclusion') == 'success'
                and run.get('repository', {}).get('full_name') == CLOUD_REPO
                and run.get('head_repository', {}).get('full_name') == CLOUD_REPO, 'Untrusted staging workflow event')
        return str(run.get('id', '')), str(run.get('run_attempt', ''))
    return os.environ.get('STAGING_RUN_ID', ''), os.environ.get('STAGING_RUN_ATTEMPT', '')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('auto-preview', 'manual-preview', 'approved'), required=True)
    args = parser.parse_args()
    root = Path.cwd()
    try:
        run_id, attempt = ids_for_mode(args.mode)
        candidate, rows = preview(root, run_id, attempt)
        if args.mode != 'approved':
            if rows:
                print('has_targets=true')
                print('fingerprint=' + fingerprint(root, candidate, rows))
                print('staging_run_id=' + run_id)
                print('staging_run_attempt=' + attempt)
            else:
                print('has_targets=false')
            return 0
        expected = os.environ.get('APPROVED_FINGERPRINT', '')
        require(rows and re.fullmatch(r'[0-9a-f]{64}', expected) and fingerprint(root, candidate, rows) == expected,
                'Candidate, baseline, values or deployment plan changed after approval')
        create_pr(root, candidate, rows)
        return 0
    except Exception as exc:
        print(str(exc) if isinstance(exc, staging.Error) else 'AI production promotion blocked', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
