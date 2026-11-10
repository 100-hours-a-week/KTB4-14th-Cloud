"""Propose human-confirmed post-Sync baselines; never access the production cluster."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import ai_production as ai
import backend_production as backend
import backend_staging_values as values

CLOUD = backend.CLOUD_REPO
FAMILIES = {'backend': backend, 'ai': ai}
MARKER = re.compile(r'<!-- production-baseline:(\{[^\n]+\}) -->')
SHA = ai.SHA


def require(ok, message):
    values.require(ok, message)


def command(*args, cwd=None, env=None):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    require(result.returncode == 0, 'Command failed: ' + args[0])
    return result.stdout


def git(root, *args):
    return command('git', *args, cwd=root).strip()


def gh(*args, write=False):
    token = os.environ.get('CLOUD_PR_TOKEN' if write else 'CLOUD_READ_TOKEN')
    require(bool(token), 'Missing Cloud GitHub token')
    return command('gh', *args, env=dict(os.environ, GH_TOKEN=token))


def api(path):
    return values.load_json(gh('api', f'repos/{CLOUD}/{path}'))


def family(service):
    require(service in FAMILIES, 'Choose backend or ai')
    return FAMILIES[service]


def targets(service):
    return ({t: f'apps/spring-value/{t}-prod.yaml' for t in backend.TARGETS}
            if service == 'backend' else ai.TARGETS)


def read(root, ref, path):
    require(git(root, 'ls-tree', ref, '--', path).split(' ', 1)[0] == '100644',
            'Expected tracked regular file: ' + path)
    return command('git', 'show', f'{ref}:{path}', cwd=root)


def ancestor(root, old, new):
    require(SHA.fullmatch(old or '') and SHA.fullmatch(new or ''), 'Invalid Git revision')
    require(subprocess.run(['git', 'merge-base', '--is-ancestor', old, new], cwd=root,
                           capture_output=True).returncode == 0, 'Revision is not in current main history')


def baseline(service, text):
    # Reuse both production validators rather than maintain another schema.
    module = family(service)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / module.BASELINE
        path.parent.mkdir(parents=True)
        path.write_text(text)
        data = module.production_baseline(root)
    require(data is not None and type(data['schema_version']) is int, 'Missing baseline')
    return data


def image(service, text):
    doc = values.parse_yaml(text)[0] if service == 'backend' else ai.staging.yaml_tool().load(text)
    require(isinstance(doc, dict) and isinstance(doc.get('image'), dict), 'Invalid image values')
    return doc['image']


def trusted_promotion(root, service, number, main='HEAD'):
    """Bind a merged allowlisted values diff to its successful promotion and Git candidate."""
    module = family(service)
    require(re.fullmatch(r'[1-9][0-9]{0,9}', str(number)), 'Invalid promotion PR number')
    number = int(number)
    pr = api(f'pulls/{number}')
    require(pr.get('merged') is True and pr.get('base', {}).get('ref') == 'main'
            and pr.get('base', {}).get('repo', {}).get('full_name') == CLOUD
            and pr.get('head', {}).get('repo', {}).get('full_name') == CLOUD,
            'Expected a merged production PR in Cloud main')
    match = re.fullmatch(('backend' if service == 'backend' else 'ai')
                         + r'-production/([1-9][0-9]*)-([1-9][0-9]*)', pr['head'].get('ref', ''))
    require(match is not None, 'Not a generated production promotion branch')
    run_id, attempt = match.groups()
    workflow_path = f'.github/workflows/{service}-production.yml'
    workflow = api(f'actions/workflows/{service}-production.yml')
    run = api(f'actions/runs/{run_id}/attempts/{attempt}')
    require(workflow.get('path') == workflow_path and type(workflow.get('id')) is int
            and run.get('workflow_id') == workflow['id'] and run.get('path') == workflow_path
            and str(run.get('id')) == run_id and str(run.get('run_attempt')) == attempt
            and run.get('event') in ('workflow_run', 'workflow_dispatch')
            and run.get('head_branch') == 'main' and run.get('status') == 'completed'
            and run.get('conclusion') == 'success'
            and run.get('repository', {}).get('full_name') == CLOUD
            and run.get('head_repository', {}).get('full_name') == CLOUD,
            'Promotion was not a successful trusted Cloud main workflow attempt')
    merge, head = pr.get('merge_commit_sha'), pr['head'].get('sha')
    ancestor(root, merge, git(root, 'rev-parse', main))
    require(SHA.fullmatch(head or ''), 'Invalid promotion head')
    git(root, 'fetch', 'origin', f'refs/pull/{number}/head')
    require(git(root, 'rev-parse', 'FETCH_HEAD') == head, 'Promotion head changed')
    before = git(root, 'merge-base', pr['base']['sha'], head)
    ancestor(root, run.get('head_sha'), before)
    names = git(root, 'diff', '--no-renames', '--name-only', before, head, '--').splitlines()
    mapping = targets(service)
    selected = {t: name for t, name in mapping.items() if name in names}
    require(selected and set(names) == set(selected.values()), 'Promotion changed non-allowlisted files')
    record_path = values.RECORD if service == 'backend' else ai.RECORD
    raw_candidate = values.load_json(read(root, before, record_path))
    candidate = (values.validate_candidate(raw_candidate) if service == 'backend'
                 else ai.staging.validate(raw_candidate, ai.staging.repository(root)))
    require(read(root, before, record_path) == read(root, head, record_path), 'Promotion rewrote candidate record')
    # Current staging may have moved on. The immutable record at promotion time is authoritative.
    for target, name in selected.items():
        old, new = read(root, before, name), read(root, head, name)
        if service == 'backend':
            values.compare_values(old, new, candidate)
        else:
            a, b = ai.staging.yaml_tool().load(old), ai.staging.yaml_tool().load(new)
            a['image'].pop('digest'); b['image'].pop('digest')
            require(a == b, 'Promotion changed more than AI image.digest')
        expected = image(service, new)
        require(expected['repository'] == candidate['image_repository']
                and expected['digest'] == candidate['image_digest'], 'Promotion image differs from Git candidate')
        require(expected != image(service, old), 'Promotion image did not change')
        require(image(service, read(root, merge, name)) == expected,
                'Merged promotion image differs from reviewed PR')
        require(image(service, read(root, main, name)) == expected,
                'Current main target image changed or rolled back: ' + target)
    return {'service': service, 'number': number, 'merge': merge, 'candidate': candidate,
            'targets': selected, 'url': f'https://github.com/{CLOUD}/pull/{number}'}


def updated_baseline(service, before, context, actor, timestamp):
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:\[bot\])?', actor or ''), 'Missing verifier identity')
    confirmed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    require(confirmed.utcoffset() == timezone.utc.utcoffset(confirmed), 'Confirmation time must be UTC')
    after = deepcopy(before)
    proof = {'promotion_pr': context['number'], 'candidate': context['candidate'],
             'confirmation': 'operator_dispatch'}
    for target in context['targets']:
        after['deployments'][target] = dict(source_sha=context['candidate']['source_sha'],
            image_digest=context['candidate']['image_digest'], confirmed_by=actor,
            confirmed_at=timestamp, evidence=json.dumps(proof, ensure_ascii=False, sort_keys=True))
    baseline(service, json.dumps(after))
    return after


def applied(data, context):
    return all(data['deployments'][t]['source_sha'] == context['candidate']['source_sha']
               and data['deployments'][t]['image_digest'] == context['candidate']['image_digest']
               for t in context['targets'])


def verify_proposal(context, before, proposed):
    selected = set(context['targets'])
    changed = {t for t in before['deployments'] if before['deployments'][t] != proposed['deployments'][t]}
    require(changed == selected, 'Baseline PR must update exactly the promotion targets')
    verifier = set()
    for target in selected:
        entry = proposed['deployments'][target]
        require(entry['source_sha'] == context['candidate']['source_sha']
                and entry['image_digest'] == context['candidate']['image_digest'], 'Baseline version not from promotion')
        proof = values.load_json(entry['evidence'])
        require(proof.get('promotion_pr') == context['number']
                and proof.get('candidate') == context['candidate'], 'Baseline evidence promotion mismatch')
        # Existing proposals may carry the evidence collected by the old workflow.
        require(proof.get('confirmation') == 'operator_dispatch'
                or (isinstance(proof.get('observed'), dict)
                    and isinstance(proof.get('functional_evidence'), str)), 'Missing operator confirmation')
        verifier.add((entry['confirmed_by'], entry['confirmed_at']))
        confirmed = datetime.fromisoformat(entry['confirmed_at'].replace('Z', '+00:00'))
        require(confirmed.utcoffset() == timezone.utc.utcoffset(confirmed), 'Confirmation time must be UTC')
    require(len(verifier) == 1, 'Inconsistent verifier/time')


def existing_pr(branch):
    pages = values.load_json(gh('api', '--paginate', '--slurp',
        f'repos/{CLOUD}/pulls?state=all&base=main&head=100-hours-a-week:{branch}&per_page=100'))
    matches = [pr for page in pages for pr in page]
    require(len(matches) <= 1, 'Multiple baseline PRs for one promotion')
    return matches[0] if matches else None


def summary(text):
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write(text + '\n')


def assert_main(root):
    require(api('git/ref/heads/main')['object']['sha'] == git(root, 'rev-parse', 'HEAD'),
            'Main changed during validation; rerun against latest main')


def propose(root, service, number, actor):
    require(not git(root, 'status', '--porcelain=v1'), 'Expected clean latest-main checkout')
    assert_main(root)
    context = trusted_promotion(root, service, number)
    name = family(service).BASELINE
    before = baseline(service, (root / name).read_text())
    branch = f'production-baseline/{service}-pr-{context["number"]}'
    prior = existing_pr(branch)
    if applied(before, context):
        summary('기준선 반영 완료: ' + context['url'])
        return None
    if prior:
        require(prior.get('state') == 'open', 'Previous baseline PR is closed; review manually')
        check_pr(root, prior['number'])
        summary('기존 기준선 PR: ' + prior['html_url'])
        return prior['html_url']
    timestamp = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    proposed = updated_baseline(service, before, context, actor, timestamp)
    # If push succeeded but PR creation failed, recover the immutable branch. Never force-push.
    remote = git(root, 'ls-remote', '--heads', 'origin', f'refs/heads/{branch}')
    if remote:
        git(root, 'fetch', 'origin', f'refs/heads/{branch}')
        saved = git(root, 'rev-parse', 'FETCH_HEAD')
        base = git(root, 'merge-base', 'HEAD', saved)
        require(git(root, 'diff', '--name-only', base, saved, '--').splitlines() == [name],
                'Recovery branch has unrelated files')
        proposed = baseline(service, read(root, saved, name))
        require(baseline(service, read(root, base, name)) == before, 'Recovery baseline became stale')
    verify_proposal(context, before, proposed)
    body = pr_body(context, before, proposed)
    assert_main(root)
    if not remote:
        git(root, 'switch', '-c', branch)
        (root / name).write_text(json.dumps(proposed, ensure_ascii=False, indent=2) + '\n')
        require(git(root, 'diff', '--name-only').splitlines() == [name], 'Only baseline JSON may change')
        git(root, 'diff', '--check')
        git(root, 'config', 'user.name', 'github-actions[bot]')
        git(root, 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
        git(root, 'add', '--', name)
        git(root, 'commit', '-m', f'chore: {service} 운영 배포 기준선 갱신 (승격 #{number})')
        # Separate token, supplied only by the trusted dispatch job, is used by Git as well as gh.
        git(root, 'push', 'origin', f'HEAD:refs/heads/{branch}')
    # A moved main never causes an automatic update; the saved branch can be recovered on retry.
    require(api('git/ref/heads/main')['object']['sha'] == git(root, 'rev-parse', 'origin/main'),
            'Main moved before PR creation; rerun')
    prior = existing_pr(branch)
    if prior:
        require(prior.get('state') == 'open', 'Baseline PR was closed')
        return prior['html_url']
    url = gh('pr', 'create', '--repo', CLOUD, '--base', 'main', '--head', branch,
             '--title', f'[Deploy] {service} 운영 배포 기준선 갱신 (승격 #{number})',
             '--body', body, write=True).strip()
    summary('Cloud 팀 수동 리뷰·병합 대기: ' + url)
    return url


def pr_body(context, before, proposed):
    marker = json.dumps({'schema_version': 1, 'service': context['service'], 'promotion_pr': context['number']})
    lines = [f'<!-- production-baseline:{marker} -->', '## 운영 배포 후 기준선 갱신', '',
             '승격 PR: ' + context['url'], '',
             '### Actions가 확인한 Git 정보',
             '- 동일 Cloud main에 병합된 승격 PR, 성공한 승격 workflow 실행 및 Git 후보 기록을 대조했습니다.',
             '- 최신 main values와 후보를 대조했습니다. 클러스터나 기능은 Actions가 검사하지 않았습니다.', '',
             '| 앱 | 이전 digest | 새 digest |', '|---|---|---|']
    for t in context['targets']:
        entry = proposed['deployments'][t]
        lines.append(f"| {t} | `{before['deployments'][t]['image_digest']}` | `{entry['image_digest']}` |")
    entry = proposed['deployments'][next(iter(context['targets']))]
    lines += ['', '### 운영 확인 기록',
              f"- 수동 실행자: `{entry['confirmed_by']}`, UTC `{entry['confirmed_at']}`",
              '- 담당자는 운영 Sync 및 Pod·기능 확인을 마친 뒤 이 워크플로를 실행합니다.', '',
              '### 병합 전제',
              '- Cloud 팀이 검토 후 수동 병합합니다.',
              '- `Production baseline freshness`를 필수 검사로 지정하고 최신 main 브랜치 요구(strict)를 설정해야 합니다.',
              '- 운영 values 변경/롤백 뒤에는 최신 main으로 갱신하고 검사를 다시 통과해야 합니다.',
              '- 기존 CloudFormation CD가 main의 deploy/** 변경을 감지하므로 병합 시 해당 실행도 확인합니다.']
    body = '\n'.join(lines) + '\n'
    require(len(body.encode()) < 60000, 'Baseline PR evidence is too large')
    return body


def check_pr(root, number):
    """Read PR blobs as data using trusted main code, not code from the PR branch."""
    pr = api(f'pulls/{number}')
    require(pr.get('base', {}).get('ref') == 'main'
            and pr.get('base', {}).get('repo', {}).get('full_name') == CLOUD
            and pr.get('head', {}).get('repo', {}).get('full_name') == CLOUD, 'Unexpected baseline PR repository')
    head = pr['head']['sha']
    require(SHA.fullmatch(head or ''), 'Invalid baseline PR head')
    git(root, 'fetch', 'origin', f'refs/pull/{number}/head')
    require(git(root, 'rev-parse', 'FETCH_HEAD') == head, 'Baseline PR head changed')
    base = git(root, 'merge-base', 'HEAD', head)
    names = git(root, 'diff', '--no-renames', '--name-only', base, head, '--').splitlines()
    changed_baselines = [s for s, mod in FAMILIES.items() if mod.BASELINE in names]
    if not changed_baselines:
        return
    require(len(changed_baselines) == 1, 'Only one baseline family per PR')
    service = changed_baselines[0]
    name = family(service).BASELINE
    require(names == [name], 'Baseline PR must change exactly one JSON file')
    matches = MARKER.findall(pr.get('body') or '')
    require(len(matches) == 1, 'Missing baseline PR provenance')
    metadata = values.load_json(matches[0])
    require(set(metadata) == {'schema_version', 'service', 'promotion_pr'}
            and type(metadata['schema_version']) is int and metadata['schema_version'] == 1
            and metadata['service'] == service, 'Invalid baseline PR provenance')
    context = trusted_promotion(root, service, metadata['promotion_pr'])
    require(pr['head']['ref'] == f'production-baseline/{service}-pr-{context["number"]}', 'Unexpected baseline branch')
    current = baseline(service, read(root, 'HEAD', name))
    require(baseline(service, read(root, base, name)) == current,
            'Baseline changed since PR was opened; review against latest main')
    proposed = baseline(service, read(root, head, name))
    verify_proposal(context, current, proposed)
    assert_main(root)
    summary('최신 main 기준선·운영 values 대조 통과: ' + pr['html_url'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('create', 'check'))
    parser.add_argument('--pr')
    args = parser.parse_args()
    try:
        root = Path.cwd()
        if args.mode == 'check':
            require(re.fullmatch(r'[1-9][0-9]*', args.pr or ''), 'Invalid PR number')
            check_pr(root, int(args.pr))
        else:
            require(os.environ.get('GITHUB_REPOSITORY') == CLOUD
                    and os.environ.get('GITHUB_REF') == 'refs/heads/main'
                    and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', 'Only Cloud main manual dispatch is allowed')
            propose(root, os.environ.get('SERVICE'), os.environ.get('PROMOTION_PR'),
                    os.environ.get('GITHUB_ACTOR', ''))
        return 0
    except Exception as exc:
        print(str(exc) if isinstance(exc, (values.ValidationError, ai.staging.Error))
              else 'Production baseline update blocked; check Git records and workflow inputs', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
