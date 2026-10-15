# RDS MySQL 신규 구축

## 상태와 범위

| 항목 | 상태 |
|---|---|
| RDS·DB Subnet Group·DB Parameter Group 선언 | 작성 완료 |
| 기존 네트워크 ID 및 사용자 지정 사양 반영 | 완료 |
| 원본 MySQL 버전·문자셋·콜레이션·시간대 확인 | 사용자 제공 SQL 결과 반영 |
| 대상 버전·클래스의 gp3·Multi-AZ·암호화 지원 | 지정 조회 명령에 대한 사용자 제공 결과 일부 확인 |
| cfn-lint·배포 설정 검증·전체 테스트 | 통과, 테스트 72개 |
| 서브넷 AZ·라우팅·SG 실제 규칙·IAM 적용 확인 | 미완료 |
| CREATE Change Set·AWS 리소스 생성·접속 검증 | 미수행 |

## 생성할 리소스

DB 작업 **신규 CREATE**입니다.
스택 이름은 `audigo-db`이며, 다음 리소스 3개를 생성하도록 선언했습니다.

| 논리 ID | 유형 | 생성할 이름 / 설정 |
|---|---|---|
| `DBInstance` | `AWS::RDS::DBInstance` | `audigo-prd-db`, RDS MySQL |
| `DBSubnetGroup` | `AWS::RDS::DBSubnetGroup` | `audigo-prd-db-subnets`, 기존 private subnet 2개 참조 |
| `DBParameterGroup` | `AWS::RDS::DBParameterGroup` | `audigo-prd-db-parameters`, family `mysql8.4` |

물리 이름은 현재 IAM의 허용 ARN과 일치하도록 고정했습니다. 이름을 바꿀 때는 템플릿과 IAM도 함께 검토합니다.

## 기존 네트워크 참조

| 항목 | ID |
|---|---|
| 리전 | `ap-northeast-2` |
| 계정 기준 | `185489190599` |
| VPC | `vpc-04129874f9fd135c6` |
| private subnet A | `subnet-0ea8a5914af73dc2b` |
| private subnet B | `subnet-05b3ce96ae987cbb1` |
| DB 보안 그룹 | `sg-079261f4850dacc68` |

VPC, private subnet, DB 보안그룹은 미리 만든 서비스를 사용했고, 새로 생성하거나 편입하지 않습니다.
기존 SG의 인바운드·아웃바운드 규칙도 이 템플릿에서는 변경하지 않습니다.

기존 ID를 직접 입력받으므로 현재 DB 스택의 `depends_on`은 빈 배열입니다.
템플릿의 Rules는 두 서브넷과 SG가 선택한 VPC에 속하는지, 두 서브넷 ID가 서로 다른지 검사합니다

## RDS 사양과 원본 호환 설정

### 사용자 지정 및 원본 조회 기반 값

| 항목 | 선언한 값 |
|---|---|
| DB 엔진 / 버전 | MySQL `8.4.11` |
| DB Parameter Group family | `mysql8.4` |
| 인스턴스 클래스 | `db.t4g.small` |
| 저장소 | `gp3`, 30GiB |
| Multi-AZ | 활성 |
| Read Replica | 생성하지 않음 |
| 자동 백업 보관 | 7일 |
| 저장 암호화 | 활성, AWS 관리형 `aws/rds` 키 사용 |
| 접속 | `PubliclyAccessible=false`, IPv4, TCP 3306 |
| 문자셋 | `utf8mb4` |
| 콜레이션 | `utf8mb4_0900_ai_ci` |
| 시간대 | `UTC` |

원본 SQL 결과는 다음과 같습니다.

```text
VERSION()              8.4.11
character_set_server   utf8mb4
collation_server       utf8mb4_0900_ai_ci
time_zone              SYSTEM
system_time_zone       UTC
```

대상 RDS에는 시간대를 `UTC`로 명시했습니다. 이 설정은 서버 기본값이며 기존 테이블의 문자셋·콜레이션이나 앱 세션 시간대까지 일괄 변환하는 작업은 아닙니다.

사용자는 `8.4.11 / db.t4g.small`을 지정한 서울 리전 조회에 대해 아래 결과를 제공했습니다.

```json
{
  "StorageType": "gp3",
  "MultiAZCapable": true,
  "SupportsStorageEncryption": true,
  "MinStorageSize": 20
}
```

이에 따라 대상 버전을 `8.4.11`로 입력했습니다. 제공된 결과에는 최대 용량과 지원 AZ가 없으므로 실제 배포 전에 전체 응답을 함께 확인합니다.

### 템플릿에서 추가한 기본값

아래 항목은 사용자 지정 사양과 구분되는 현재 코드의 기본값입니다.

| 항목 | 기본값 |
|---|---|
| 관리자 계정 | `audigo_admin` |
| 관리자 비밀번호 | `ManageMasterUserPassword=true`, RDS가 Secrets Manager로 관리 |
| 관리 Secret 암호화 | 기본 AWS 관리형 `aws/secretsmanager` 키 |
| 백업 시간 | 매일 03:00–03:30 KST, 파라미터 `18:00-18:30` UTC |
| 유지보수 시간 | 월요일 04:00–05:00 KST, 파라미터 `sun:19:00-sun:20:00` UTC |
| 자동 minor 업그레이드 | 비활성 |
| major 업그레이드 허용 | 비활성 |
| Extended Support 설정 | `open-source-rds-extended-support-disabled` |
| 저장 자동 확장 | 설정하지 않음 |
| 스냅샷 태그 복사 | 활성 |
| 삭제 보호 | 활성 |
| DB 삭제 시 자동 백업 삭제 | `DeleteAutomatedBackups=false` |

고객 관리형 KMS 키(CMK)를 생성하거나 참조하는 구성은 현재 포함하지 않았습니다.
도입 시에는 키 ARN·키 정책·실행 역할 권한을 함께 변경합니다.

관리자 계정은 앱 계정과 분리합니다. 관리자 비밀번호는 파라미터 JSON이나 Git에 저장하지 않습니다.

## 파일별 구현 범위

아래 경로는 Cloud 저장소 루트 기준입니다.

| 파일 | 반영 내용 |
|---|---|
| `cloudformation/db.yaml` | RDS·두 그룹·기존 네트워크 검증 Rules·보존 정책·Outputs |
| `deploy/parameters/db.json` | 실제 네트워크 ID, DB 버전·사양·문자셋·시간대·운영 시간대 입력 |
| `deploy/stacks.json` | `audigo-db`를 Platform 실행 역할 그룹에 연결 |
| `bootstrap/infra-iam.yaml` | DB 스택 접근과 Platform의 RDS·Secret·KMS 권한 추가 |
| `docs/rds-create.md` | 구축 내용 정리 |
| `docs/iam-group-permissions.md` | DB 권한 범위 및 운영 절차 설명 |
| `docs/infrastructure-cicd.md` | DB 스택의 현재 상태와 생성 가이드 연결 |
| `tests/test_infra_db.py` | DB 보존·접속·암호화·역할별 권한 경계 검사 추가 |
| `tests/test_iam_bootstrap.py` | DB 권한 추가에 맞춰 기존 보안 검사 갱신 |
| `tests/test_iam_groups.py` | DB의 Platform 그룹 매핑 검사 갱신 |
| `tests/test_infra_contract.py` | 전체 스택 매핑 검사에 DB 반영 |

### 현재 배포 설정

```json
{
  "name": "audigo-db",
  "template": "cloudformation/db.yaml",
  "parameters": "deploy/parameters/db.json",
  "role_group": "platform",
  "enabled": true,
  "allow_create": true,
  "depends_on": [],
  "capabilities": []
}
```

~~현재는 검토용 비활성 상태입니다. 사전 확인과 IAM 반영 이후 최초 생성 시에만 `enabled=true`, `allow_create=true`로 변경합니다.~~
최초 생성 완료 후 `allow_create=false`로 되돌리고, 이후 스택 관리를 위해 `enabled=true`는 유지합니다.

## IAM과 비밀번호 관리

[그룹별 IAM 운영 가이드](KTB4-14th-Cloud/docs/iam-group-permissions.md)의 역할 분리를 따릅니다.

```text
GitHub Actions
  └─ AudigoGitHubInfraDeployRole
       ├─ iac-plan: DB Change Set 생성·조회, Platform 실행 역할 전달
       └─ iac-production: 검토한 동일 Change Set 실행·조회
            └─ AudigoCfnPlatformExecutionRole
                 ├─ 지정한 DB·Subnet Group·Parameter Group 관리
                 ├─ 기존 네트워크 조회
                 ├─ RDS 관리형 Secret 생성·태그
                 └─ AWS 관리형 암호화 키 사용
```

- RDS 관리 권한은 `audigo-prd-db`와 위 두 그룹의 ARN으로 제한합니다.
- DB 생성·수정의 그룹 연결 인가를 위해 두 그룹과 기본 option group `default:mysql-8-4`를 허용합니다. option group 자체를 수정하는 API는 추가하지 않았습니다.
- RDS가 관리형 Secret 이름을 정하므로 Secret 생성·태그는 같은 계정·리전의 `rds!db-*` 패턴으로 제한합니다. 특정 DB의 Secret 하나만으로 제한된 정책은 아닙니다.
- GitHub·Platform 역할에는 `secretsmanager:GetSecretValue`를 부여하지 않습니다. 실제 관리자 접속에 필요한 Secret 조회 권한은 운영자에게 별도로 필요합니다.
- KMS 권한은 AWS 관리형 키 alias와 RDS 경유 grant 조건으로 제한합니다.
- `AWSServiceRoleForRDS`가 계정에 있는 것을 확인했습니다.

IAM 템플릿은 일반 DB 배포 대상에 포함되지 않습니다. 관리자가 별도 bootstrap 스택의 Change Set으로 정책 변경을 반영하고 실제 역할·권한 경계·SCP를 검증합니다.