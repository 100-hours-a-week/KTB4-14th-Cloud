# 기존 ALB 편입

## 상태와 범위

2026-10-13 사용자가 제공한 AWS CLI 결과를 기준으로 작성했습니다.
당시 ALB·리스너·대상 그룹의 소유 스택 조회에서 `Stack ... does not exist`가 반환됐습니다.
2026-10-14 추가 결과에서 `audigo-alb`의 `IMPORT_COMPLETE`, 리소스 3개의 `UPDATE_COMPLETE`,
아래 기존 ARN 유지를 확인했습니다. 최신 Drift와 HTTPS 동작은 별도 검증입니다.

| 논리 ID | 유형 | 기존 이름/설정 |
|---|---|---|
| `AudigoBackendAlb` | `AWS::ElasticLoadBalancingV2::LoadBalancer` | `audigo-backend-alb`, internet-facing, application, ipv4 |
| `AudigoFargateTargetGroup` | `AWS::ElasticLoadBalancingV2::TargetGroup` | `audigo-v1-Fargate`, HTTP:8080, HTTP1, ip, ipv4 |
| `AudigoHttpsListener` | `AWS::ElasticLoadBalancingV2::Listener` | HTTPS:443, 아래 기본 인증서·정책 사용 |

기존 ARN (Import 시 해당 논리 ID에 연결):

```text
AudigoBackendAlb
arn:aws:elasticloadbalancing:ap-northeast-2:185489190599:loadbalancer/app/audigo-backend-alb/38d9b782b5383394

AudigoFargateTargetGroup
arn:aws:elasticloadbalancing:ap-northeast-2:185489190599:targetgroup/audigo-v1-Fargate/53f2677044a1644f

AudigoHttpsListener
arn:aws:elasticloadbalancing:ap-northeast-2:185489190599:listener/app/audigo-backend-alb/38d9b782b5383394/ab81d9c379ba9263
```

이전 IaC 스냅샷의 대상 그룹/리스너 ARN은 이번 조회와 다릅니다.
이번 작업에는 위 ARN만 사용하며 예전 대상 그룹, HTTP:80 리스너 또는 경로 규칙을 생성하지 않습니다.

## 기존 참조와 설정

| 항목 | 값 |
|---|---|
| VPC | `vpc-04129874f9fd135c6` |
| 서브넷 | `subnet-06bb5908818e1bc74`, `subnet-0b9604514010cbb54` |
| 보안 그룹 | `sg-0b11224bd0d8f6750` |
| ACM 인증서 | `arn:aws:acm:ap-northeast-2:185489190599:certificate/09c54455-a23b-4a9b-b0f4-422e3746fc70` |
| TLS 정책 | `ELBSecurityPolicy-TLS13-1-2-Res-PQ-2025-09` |
| mTLS | off |
| 태그 | 3개 리소스 모두 없음 |
| 헬스 체크 | HTTP / traffic-port / `/actuator/health` / 코드 200 |
| 체크 간격 / 제한 시간 | 30초 / 5초 |
| 정상 / 비정상 임계값 | 3회 / 3회 |

네트워크 ID는 타입이 지정된 파라미터의 기본값으로 유지합니다.
이 파라미터들은 **기존 리소스를 참조**하는 것이지 VPC/서브넷/보안 그룹을 이 스택에 편입하는 것이 아닙니다.
초기 Import에서는 기본값을 그대로 사용합니다. 인증서는 ARN으로 참조하며 새로 발급하지 않습니다.

기본 규칙은 대상 그룹 하나에 가중치 1로 전달합니다. 그룹 고정 세션은 false/3600초이며
별도 경로 조건이나 변환은 없습니다. 기본 규칙은 `DefaultActions`로 선언하며 별도의
`ListenerRule` 리소스로 편입하지 않습니다.
동일 인증서가 인증서 목록에 default/non-default로 두 번 반환됐지만 고유 ARN은 하나입니다.
기본 인증서는 한 번만 선언하며 별도 `ListenerCertificate`를 생성하지 않습니다.

## 속성 처리 원칙

- ALB: idle timeout 60초, keep-alive 3600초, HTTP2 true, 삭제 보호 false,
  XFF append, desync defensive 등 비어 있지 않은 변경 가능한 조회 값을 명시합니다.
- 로그(access/health-check/connection)는 모두 false입니다. 빈 bucket/prefix는 선언하지 않습니다.
- ALB의 cross-zone true는 변경할 수 없는 속성이므로 선언하지 않습니다.
- `EnablePrefixForIpv6SourceNat: off`는 ALB IPv4 구성에 적용할 생성 속성으로 선언하지 않습니다.
- 대상 그룹: deregistration 300초, round_robin, slow start 0, stickiness false,
  cross-zone use_load_balancer_configuration 및 정상 대상 임계값을 유지합니다.
  비활성 app-cookie의 빈 cookie_name은 제외합니다.
- 리스너: server 헤더 true만 명시하고, 빈 커스텀 헤더 설정은 제외합니다.
- ECS 태스크 IP는 `Targets`에 고정하지 않습니다. 등록과 해제는 ECS 서비스가 담당합니다.
- 생략한 비활성 속성/등록 대상의 현재 상태 전체를 Drift Detection이 보증하는 것은 아닙니다.
  서비스 상태와 실제 속성도 별도로 비교합니다.

## 편입 순서

1. 설정과 대상 상태를 백업하고 작업 직전에 기존 스택 소유 여부를 다시 확인합니다.
2. CloudFormation 서울 리전에서 **기존 리소스 가져오기**로 새 스택을 준비합니다.
3. `cloudformation/alb.yaml`을 업로드하고 스택 이름은 `audigo-alb`로 지정합니다.
4. 위 3개 논리 ID에 실제 ARN을 연결합니다. 식별자 필드 이름은 콘솔에서 확인합니다.
5. 파라미터는 위 기존 ID의 기본값을 유지합니다.
6. 변경 세트가 리소스 **3개 Import만** 포함하는지 확인한 후 실행합니다.
7. `IMPORT_COMPLETE`, 리소스 탭의 ARN 유지 및 Drift 결과를 확인합니다.
8. HTTPS 요청, 대상 헬스와 ECS 서비스 상태가 기존과 동일한지 확인합니다.

모든 리소스는 `DeletionPolicy: Retain`, `UpdateReplacePolicy: Retain`입니다.
이 정책이 리스너·헬스 체크 등 잘못된 속성 업데이트까지 막지는 않습니다.
`Outputs`는 넣지 않았습니다. 전체 리소스 편입 완료 후 별도 UPDATE로 추가합니다.

## CI/CD

`alb.yaml`은 기존 전체 템플릿 CI 검증 대상에 포함됩니다.
`audigo-alb`는 `deploy/stacks.json`에서 `enabled: false`, `allow_create: false`이고
실행 역할도 아직 설정하지 않았습니다. 따라서 현재 CD에서는 배포하지 않습니다.
기존 UPDATE/CREATE CD로 최초 Import를 대신하지 않습니다.
편입 후 전용 실행 역할과 Plan/Apply 역할 접근 권한을 준비한 뒤 CD를 활성화합니다.

공식 근거:
- [Import 지원 목록](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/resource-import-supported-resources.html)
- [리스너 속성과 기본 규칙](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-elasticloadbalancingv2-listener.html)
- [ALB 속성](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-elasticloadbalancingv2-loadbalancer-loadbalancerattribute.html)
