{{- define "twitch-stream-recorder.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "twitch-stream-recorder.sanitizeName" -}}
{{- $name := . | replace "_" "-" | trunc 63 | trimSuffix "-" -}}
{{- if not (hasPrefix $name "-") -}}
{{- $name -}}
{{- else -}}
{{- printf "job-%s" $name -}}
{{- end -}}
{{- end -}}

{{- define "twitch-stream-recorder.fullname" -}}
{{- printf "%s-%s" (include "twitch-stream-recorder.name" .) .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "twitch-stream-recorder.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "twitch-stream-recorder.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion }}
app.kubernetes.io/component: {{ .Values.component }}
{{- end -}}
