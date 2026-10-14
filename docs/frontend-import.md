# 기존 프론트엔드 리소스 편입

## 상태와 범위

설정 기준은 2026-10-13 사용자가 제공한 AWS CLI 결과입니다.
당시 서울·버지니아의 버킷·배포·OAC 소유 스택 조회는 ValidationError를 반환했습니다.
2026-10-14 추가 결과에서 `audigo-frontend`의 `IMPORT_COMPLETE`, 리소스 4개의
`UPDATE_COMPLETE`, 아래 기존 ID 유지를 확인했습니다. 최신 Drift와 프론트 동작은 별도 검증입니다.
한글 복구는 보류하며, 최초 실패·재시도 기록은 아래에 유지합니다.

| 논리 ID | 리소스 유형 | Import에 사용할 기존 식별자 값 |
|---|---|---|
| `AudigoFrontendBucket` | `AWS::S3::Bucket` | `audigo-front` |
| `AudigoFrontendBucketPolicy` | `AWS::S3::BucketPolicy` | `audigo-front` (정책이 연결된 버킷) |
| `AudigoFrontendOriginAccessControl` | `AWS::CloudFront::OriginAccessControl` | `E2AR3ACWP17KPF` |
| `AudigoFrontendDistribution` | `AWS::CloudFront::Distribution` | `E2XADD6BSMHBKF` |

최초 편입에서는 `Outputs` 없이 **4개 리소스**를 Import합니다.
정책은 버킷 속성이 아니라 별도의 리소스로 편입합니다.
식별자 필드 이름은 CloudFormation 콘솔 또는 템플릿 요약에서 확인합니다.
일반 CREATE로 배포하지 않습니다.

## 기존 설정

### S3

- 버킷: `audigo-front`, 리전: `ap-northeast-2`
- 태그: 없음 (`NoSuchTagSet`)
- 기본 암호화: `AES256`, `BucketKeyEnabled: true`
- **SSE-C 업로드 차단**: `BlockedEncryptionTypes.EncryptionType: [SSE-C]` 유지
- 소유권: `BucketOwnerEnforced`, 별도의 ACL 속성을 선언하지 않음
- 퍼블릭 액세스 차단 4개 모두 true
- 버전 관리·로깅·알림 조회에 설정이 반환되지 않음: 활성화 설정을 임의로 추가하지 않음
- CORS·웹사이트·수명 주기·복제: 각각 설정 없음 오류가 확인되어 선언하지 않음
- 기존 객체 및 프론트엔드 배포 파일은 Import 작업에서 업로드하거나 삭제하지 않음

Bucket Key는 SSE-KMS에 효과가 있는 옵션이지만 실제 조회된 true를 소유권 이전 과정에서
임의로 정리하지 않습니다. `VersioningConfiguration: Suspended`도 넣지 않습니다.
버전 관리를 사용한 적 없는 상태와 Suspended를 동일한 것으로 취급하지 않습니다.

### 버킷 정책

기존 정책의 `Version: 2008-10-17`, `Id: PolicyForCloudFrontPrivateContent`,
`Sid: AllowCloudFrontServicePrincipal`을 유지합니다.
CloudFront 서비스에 `audigo-front/*`의 `s3:GetObject`만 허용하며
SourceArn은 `arn:aws:cloudfront::185489190599:distribution/E2XADD6BSMHBKF`입니다.
템플릿의 Ref/GetAtt/Sub는 Import 후 같은 실제 이름·ARN·배포 ID로 해석됩니다.
버킷 정책에 CloudFront 배포를 참조하지만 배포는 버킷 정책을 참조하지 않아 순환 의존성이 없습니다.

### OAC

- 이름: `oac-audigo-front.s3.ap-northeast-2.amazonaws.com-mu2ky568eka`
- 설명: `Created by CloudFront`
- 원본 유형: `s3`, 서명 방식: `sigv4`, 서명 동작: `always`
- OAI는 사용하지 않으며 `OriginAccessIdentity: ''`를 유지

### CloudFront

| 설정 | 기존 값 |
|---|---|
| ID / 상태 | `E2XADD6BSMHBKF` / Deployed |
| 도메인 / 별칭 | `d2d0ycd1pf290y.cloudfront.net` / `audigo.kr` |
| 태그 | Name = audigo |
| 설명 | 어디고 서비스의 프론트 배포입니다. |
| 기본 문서 | `index.html` |
| 원본 ID | `audigo-front.s3.ap-northeast-2.amazonaws.com-mu2edr8i4ii` |
| 원본 도메인 | `audigo-front.s3.ap-northeast-2.amazonaws.com` |
| 연결 시도 / 연결 제한 시간 / 응답 제한 시간 | 3회 / 10초 / 30초 |
| 프로토콜 정책 | redirect-to-https |
| 허용 및 캐시 HTTP 메서드 | HEAD, GET |
| 압축 / gRPC / SmoothStreaming | true / false / false |
| 캐시 정책 | `658327ea-f89d-4fab-a63d-7e88639e58f6` (기존 관리형 정책 참조) |
| 오류 응답 | 403·404 → `/index.html`, 응답 200, TTL 10초 |
| 가격 등급 / HTTP / IPv6 | PriceClass_All / http2 / true |
| 지역 제한 / Origin Shield | none / false |
| TLS 정책 / SSL 방식 | TLSv1.2_2025 / sni-only |
| 로깅 / Staging | false / false |

함수 연결, 추가 캐시 동작, 원본 그룹, 지속 배포 정책은 없습니다.
조회 응답의 Quantity/Items 래퍼, ETag, CallerReference, CertificateSource 등은
CloudFormation 생성 속성으로 복사하지 않습니다.
비활성 로깅의 빈 버킷 이름도 선언하지 않습니다.

### 별도 관리 대상 (이번에는 참조만)

- ACM: `arn:aws:acm:us-east-1:185489190599:certificate/c0c0e818-1749-4f70-9756-f156edf0c9b7`
- WAF: `arn:aws:wafv2:us-east-1:185489190599:global/webacl/CreatedByCloudFront-722e4b05/397f3726-c207-415e-bd96-7b0ecc24f184`
- DNS의 `audigo.kr` Alias 및 AWS 관리형 캐시 정책

**WAF 연결을 제거하거나 TLS 정책을 낮추지 않습니다.**
이 리소스들의 ARN/ID를 참조하는 것과 해당 리소스를 스택에 편입하는 것은 다릅니다.

## 검증 도구

`BlockedEncryptionTypes`는 2026년 도입된 속성으로, 기존 `cfn-lint 1.40.2`의 스키마보다 새 기능입니다.
기존 차단 설정을 삭제하거나 E3002 등 오류를 무시하지 않고, 공식 릴리스 `1.57.2`로 버전을 고정 갱신했습니다.
CI 검증 도구 변경은 모든 템플릿에 영향을 주므로 전체 CI 통과를 확인한 후 Import합니다.
로컬 환경에서 패키지를 설치하지 못했으므로 실제 cfn-lint 검증은 GitHub CI에서 확인해야 합니다.

## 편입 절차

1. 최신 S3/OAC/CloudFront 설정과 스택 소유 여부를 재확인하고 CI 통과를 확인합니다.
2. CloudFormation **서울 리전**에서 새 스택의 기존 리소스 가져오기를 선택합니다.
3. `cloudformation/frontend.yaml`을 업로드하고 스택 이름을 `audigo-frontend`로 지정합니다.
4. 위 표의 논리 ID 4개에 기존 식별자 값을 연결합니다.
5. 변경 세트가 **Import 4개만** 포함하는지 확인한 후 실행합니다.
6. `IMPORT_COMPLETE`, 기존 물리 ID 유지, Drift Detection 결과를 확인합니다.
7. `https://audigo.kr`, 정적 자산 및 SPA 하위 경로 접속을 확인합니다.
   CloudFront의 상태/별칭/원본/OAC/WAF/TLS, 버킷 정책·암호화·퍼블릭 차단 설정을 다시 비교합니다.

모두 `DeletionPolicy: Retain`, `UpdateReplacePolicy: Retain`입니다.
보존 정책은 잘못된 속성 업데이트까지 막아주는 보호 장치가 아닙니다.
Outputs는 전체 리소스 편입 완료 후 별도 UPDATE로 추가합니다.

## 최초 Import 실패와 복구 주의사항

### 한글이 물음표로 저장된 현상

UTF-8 출력을 지정해 다시 조회한 AWS 템플릿에서 `has_korean: False`,
`question_marks: 388`이 확인됐습니다. 로컬 YAML은 UTF-8 디코딩에 성공하고 한글이 남아 있습니다.
AWS에서 반환된 템플릿에는 실제 물음표가 들어 있지만, 업로드 과정 중 정확히 어느 지점에서
대체됐는지는 이 결과만으로 단정하지 않습니다.

우회용 **이번 재편입 전용 파일**은 `deploy/imports/frontend.json`입니다.
원본 YAML을 JSON으로 변환했으며 한글 문자열은 `\uXXXX`로 표현했습니다.
파일 바이트는 ASCII만 포함하지만 JSON 파싱 후 Description과 CloudFront Comment는 원래 한글입니다.
Ref/GetAtt/Sub는 JSON 내장 함수 표기로 보존하고, 인증서 선택 중복 수정도 포함했습니다.
Outputs는 없으며 Import 대상은 기존 4개입니다.

실패 스택을 안전하게 정리한 다음 재편입에서는 **YAML 대신 이 JSON 파일을 업로드**합니다.
내용을 터미널에서 복사하지 않고 파일 자체를 선택합니다.
JSON은 이번 수정본에서 생성한 전송용 사본입니다. 지속적인 코드 관리는
`cloudformation/frontend.yaml`을 기준으로 하며, 이후 원본 변경 시 이 사본을 그대로 사용하지 않습니다.
재편입 후 저장된 템플릿과 실제 CloudFront Comment의 한글 보존을 다시 확인합니다.

### 인증서 선택 항목 오류

사용자가 제공한 첫 실행 이벤트에서 CloudFront가 `IMPORT_COMPLETE`에 도달한 뒤
스택 태그 적용 단계에서 `UPDATE_FAILED`가 발생했습니다. 오류는 다음과 같습니다.

```text
Exactly one of [AcmCertificateArn, CloudFrontDefaultCertificate, IamCertificateId] needs to be specified
```

API 조회 응답의 `CloudFrontDefaultCertificate: false`를 템플릿에도 함께 선언한 것이 문제였습니다.
ACM을 사용하는 템플릿에서는 `AcmCertificateArn`만 남기고 나머지 인증서 선택 항목을 제거했습니다.
기존 인증서 ARN, TLS 정책, SSL 방식과 WAF 연결은 그대로입니다.
인증서 선택 항목 중복을 막는 회귀 테스트를 추가했습니다.

첫 실행의 최종 스택 상태는 `ROLLBACK_COMPLETE`입니다. 이 상태에서는 스택 삭제만 가능하므로
실패 스택을 정리한 뒤 새 Import가 필요합니다. 하지만 삭제 전 다음 사항을 확인해야 합니다.

1. `list-stack-resources`로 롤백 후 남은 스택 등록 리소스와 물리 ID를 확인합니다.
2. `get-template --template-stage Original`로 **실제로 업로드된** 템플릿의 모든 리소스가
   `DeletionPolicy: Retain`인지 확인합니다. 로컬 파일만 보고 판단하지 않습니다.
3. 실제 버킷·버킷 정책·OAC·CloudFront가 유지되고 프론트엔드가 정상인지 확인합니다.
4. 확인 및 삭제 승인 후 실패 스택만 정리합니다. 실제 AWS 리소스를 수동 삭제하지 않습니다.
5. 수정된 템플릿으로 기존 식별자를 다시 연결하고 Import를 수행합니다.

이벤트의 S3 업데이트는 스택 태그 적용/제거 과정으로 표시됐습니다.
버킷 정책은 제공된 이벤트에서 Import 완료가 확인되지 않았으므로 별도로 상태를 확인해야 합니다.
OAC 롤백 이벤트는 이번 실패의 최초 원인이 아닙니다.

- [스택 상태와 ROLLBACK_COMPLETE 제한](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/view-stack-events.html)
- [CloudFront 인증서 선택 속성](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-cloudfront-distribution-viewercertificate.html)

## CI/CD 설정

템플릿은 전체 CI 검증 대상이지만 `audigo-frontend`는 기존 설정대로
`enabled: false`, `allow_create: false`, 실행 역할 미설정 상태를 유지합니다.
현재 자동 CD로 생성/업데이트하지 않습니다. Import 완료 후 실행 역할과 Plan/Apply 접근 권한을
준비한 뒤 CD를 활성화합니다. 앱 배포의 S3 업로드/CloudFront 무효화와 인프라 설정 관리를 구분합니다.

공식 근거:
- [리소스 Import 지원](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/resource-import-supported-resources.html)
- [S3 암호화 설정](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-s3-bucket-serversideencryptionrule.html)
- [S3 원본과 OAC](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-cloudfront-distribution-s3originconfig.html)
- [검증 도구 릴리스 1.57.2](https://github.com/aws-cloudformation/cfn-lint/releases/tag/v1.57.2)
