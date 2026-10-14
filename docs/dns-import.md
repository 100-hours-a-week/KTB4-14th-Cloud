# Audigo DNS 편입

## 현재 단계

설정 기준은 2026-10-13 사용자가 제공한 AWS CLI 결과입니다.
2026-10-14 추가 결과에서 `audigo-dns`의 `IMPORT_COMPLETE`, 리소스의 `UPDATE_COMPLETE`,
기존 ID `Z01588362VOGQ6V2J27VE` 유지를 확인했습니다. 최신 Drift와 실제 DNS 동작은 별도 검증입니다.
네트워크 편입은 완료되었고 V2 신규 네트워크 추가는 추후 진행합니다.

| 항목 | 기존 설정 |
|---|---|
| 호스팅 영역 ID | `Z01588362VOGQ6V2J27VE` |
| 도메인 | `audigo.kr.` |
| 공개 여부 | 퍼블릭 (`PrivateZone: false`) |
| 설명 | 빈 문자열 |
| 태그 | 없음 |
| 레코드 수 | 8 |
| 목표 스택 | `audigo-dns` |
| 논리 ID | `AudigoHostedZone` |

## 관리 범위와 제한

`cloudformation/dns.yaml`에는 `AWS::Route53::HostedZone` **1개만** 선언합니다.
호스팅 영역 Import가 내부 레코드까지 CloudFormation 관리 대상으로 편입하는 것은 아닙니다.
현재 `AWS::Route53::RecordSet`과 `RecordSetGroup`은 CloudFormation 리소스 Import 지원 목록에 없습니다.
기존 레코드를 삭제하거나 다시 생성하지 않으며, 별도의 전환 계획이 마련되기 전까지 현 상태로 유지합니다.

- [CloudFormation Import 지원 목록](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/resource-import-supported-resources.html)
- [HostedZone 속성 문서](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-route53-hostedzone.html)

`CallerReference`, 레코드 수, 위임 네임서버는 조회 결과로 보관하는 정보이지
이 템플릿에서 생성 속성으로 주입하는 값이 아닙니다. 기존 리소스 ID는 Import 과정에서 연결합니다.
`Retain`은 스택 삭제/교체 때의 보존 정책이며, 모든 잘못된 업데이트를 막아주는 정책은 아닙니다.

## 기존 DNS 레코드 목록 (관리 대상 아님)

| 이름 | 유형 | TTL | 대상 / 값 |
|---|---|---:|---|
| `audigo.kr.` | A Alias | 대상 기준 | `d2d0ycd1pf290y.cloudfront.net.` |
| `audigo.kr.` | NS | 172800 | 아래 네임서버 4개 |
| `audigo.kr.` | SOA | 900 | `ns-451.awsdns-56.com. awsdns-hostmaster.amazon.com. 1 7200 900 1209600 86400` |
| `_85bf636e8715885025ffe828ddf315dc.audigo.kr.` | CNAME | 3600 | `_1a775bb4ccc0c19add91d7c537e8f517.wzccmgtwzk.acm-validations.aws.` |
| `api.audigo.kr.` | A Alias | 대상 기준 | `dualstack.audigo-backend-alb-1880049512.ap-northeast-2.elb.amazonaws.com.` |
| `_23765332f722906f087b4b9893f3e94f.api.audigo.kr.` | CNAME | 300 | `_e9d1e4eb041e8cb07365efc3f9738f02.wzccmgtwzk.acm-validations.aws.` |
| `staging.audigo.kr.` | A | 300 | `52.79.45.77` |
| `_4662f15f54d8a94333ba71dbb99caf2e.staging.audigo.kr.` | CNAME | 300 | `_b9bafab3f8aadec2c3621e0feb730087.wzccmgtwzk.acm-validations.aws.` |

Alias 설정:
- 메인: 대상 HostedZoneId `Z2FDTNDATAQYW2` / EvaluateTargetHealth `false`
- API: 대상 HostedZoneId `ZWKZPGTI48KDX` / EvaluateTargetHealth `false`
- 이 대상 ID들은 **Audigo 호스팅 영역 ID와 다릅니다.**

위임 네임서버 (NS 레코드 값에는 마지막 점이 붙음):
- `ns-451.awsdns-56.com`
- `ns-707.awsdns-24.net`
- `ns-1664.awsdns-16.co.uk`
- `ns-1061.awsdns-04.org`

## 편입 절차

1. 다른 CloudFormation 스택이 이 호스팅 영역을 관리하고 있지 않은지 확인합니다.
2. 작업 직전에 호스팅 영역 설정, 태그, 레코드를 다시 조회하여 백업합니다.
3. CloudFormation에서 새 스택의 **기존 리소스 가져오기**를 선택하고 `dns.yaml`을 업로드합니다.
4. 스택 이름은 `audigo-dns`, 논리 ID는 `AudigoHostedZone`, 기존 영역 ID는
   `Z01588362VOGQ6V2J27VE`로 연결합니다. 식별자 필드 이름은 콘솔/템플릿 요약에서 확인합니다.
5. 변경 세트가 해당 호스팅 영역 1개의 **Import**만 포함하는지 확인한 후 실행합니다.
6. `IMPORT_COMPLETE`, Outputs의 기존 ID 유지, Drift 결과를 확인합니다.
7. DNS 레코드 8개와 위임 네임서버가 백업과 동일한지 비교합니다.

도메인 등록 기관의 네임서버를 수정하거나 동일 이름의 새 호스팅 영역을 생성하지 않습니다.

## CI/CD

DNS 템플릿은 전체 템플릿 CI 검증 대상입니다.
`deploy/stacks.json`의 `audigo-dns`는 `enabled: false`, `allow_create: false`이므로
현재 자동 CD에서는 배포하지 않습니다. 기존 UPDATE/CREATE CD는 최초 Import를 수행하지 않습니다.

Import 성공 후 DNS 전용 실행 역할과 Plan/Apply 역할의 `audigo-dns` 접근 권한을 구성하고,
실행 역할 ARN 및 수동 실행 선택지를 추가한 뒤 DNS CD를 활성화합니다.
기존 네트워크 실행 역할은 DNS용으로 재사용하거나 무작정 권한을 확대하지 않습니다.
