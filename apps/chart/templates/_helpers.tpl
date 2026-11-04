{{/*
Deployment selector와 Service/NetworkPolicy가 Pod를 고를 때 쓰는 라벨입니다.
Deployment selector는 생성 후 바꿀 수 없으므로 릴리스마다 변하지 않는 값만 둡니다.
*/}}
{{- define "app-chart.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name | quote }}
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end }}

{{/* 리소스에 붙이는 전체 라벨입니다. 조회·모니터링용 라벨은 여기에만 둡니다. */}}
{{- define "app-chart.labels" -}}
{{ include "app-chart.selectorLabels" . }}
service: {{ required "labels.service를 지정해 주세요" .Values.labels.service | quote }}
env: {{ required "labels.env를 지정해 주세요" .Values.labels.env | quote }}
component: {{ required "labels.component를 지정해 주세요" .Values.labels.component | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
{{- end }}

{{/* ExternalSecret이 만들고 Deployment가 envFrom으로 읽는 Secret 이름 */}}
{{- define "app-chart.secretName" -}}
{{ .Release.Name }}-secrets
{{- end }}

{{/*
이미지 주소입니다. digest가 있으면 repo@digest(태그가 같이 있으면 repo:tag@digest)로,
없으면 repo:tag로 만듭니다.
*/}}
{{- define "app-chart.image" -}}
{{- $img := .Values.image -}}
{{- $ref := required "image.repository를 지정해 주세요" $img.repository -}}
{{- with $img.tag }}{{ $ref = printf "%s:%s" $ref (toString .) }}{{ end -}}
{{- with $img.digest -}}
{{- if not (hasPrefix "sha256:" (toString .)) }}{{ fail "image.digest는 sha256:으로 시작해야 합니다" }}{{ end -}}
{{- $ref = printf "%s@%s" $ref . -}}
{{- end -}}
{{- if not (or $img.tag $img.digest) }}{{ fail "image.tag 또는 image.digest를 지정해 주세요" }}{{ end -}}
{{- $ref -}}
{{- end }}

{{/* 프로브 이름(startup/readiness/liveness)을 받아 실제 HTTP 경로를 돌려줍니다. 개별 path가 없으면 probes.path를 씁니다. */}}
{{- define "app-chart.probePath" -}}
{{- $probe := index .root.Values.probes .name | default dict -}}
{{- $probe.path | default .root.Values.probes.path -}}
{{- end }}

{{/* 값이 빈 문자열인 키를 빼고 YAML로 출력합니다. resources처럼 일부만 채운 맵에 사용합니다. */}}
{{- define "app-chart.compact" -}}
{{- $out := dict -}}
{{- range $k, $v := . -}}
{{- if or $v (eq (toString $v) "0") -}}
{{- $_ := set $out $k $v -}}
{{- end -}}
{{- end -}}
{{- if $out -}}
{{- toYaml $out -}}
{{- end -}}
{{- end }}
