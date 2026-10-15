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

{{- define "twitch-stream-recorder.secretName" -}}
{{- .Values.secrets.existingSecret | default .Values.secrets.name -}}
{{- end -}}

{{- define "twitch-stream-recorder.recordingsClaim" -}}
{{- .Values.storage.existingClaim | default "twitch-rec-storage" -}}
{{- end -}}

{{/* Twitch credentials from the chart's Secret or secrets.existingSecret. */}}
{{- define "twitch-stream-recorder.twitchEnv" -}}
- name: client_id
  valueFrom:
    secretKeyRef:
      name: {{ include "twitch-stream-recorder.secretName" . }}
      key: client_id
- name: client_secret
  valueFrom:
    secretKeyRef:
      name: {{ include "twitch-stream-recorder.secretName" . }}
      key: client_secret
- name: twitch_oauth_token
  valueFrom:
    secretKeyRef:
      name: {{ include "twitch-stream-recorder.secretName" . }}
      key: twitch_oauth_token
      optional: true
{{- end -}}

{{/* Pod identity for wide events (k8s.pod.name, k8s.node.name, ...). */}}
{{- define "twitch-stream-recorder.podEnv" -}}
- name: POD_NAME
  valueFrom:
    fieldRef:
      fieldPath: metadata.name
- name: POD_NAMESPACE
  valueFrom:
    fieldRef:
      fieldPath: metadata.namespace
- name: NODE_NAME
  valueFrom:
    fieldRef:
      fieldPath: spec.nodeName
- name: APP_VERSION
  value: {{ .Values.image.tag | quote }}
- name: EVENT_SINKS
  value: {{ .Values.events.sinks | quote }}
{{- end -}}

{{/*
The recording Job the controller copies for each live broadcast. The controller
adds the name, labels, owner and the --login/--quality/--stream-id/--container
arguments; everything else about the pod is decided here.
*/}}
{{- define "twitch-stream-recorder.jobTemplate" -}}
metadata:
  labels:
    app.kubernetes.io/name: twitch-stream-recorder
    app.kubernetes.io/instance: {{ .Release.Name }}
spec:
  backoffLimit: {{ .Values.operator.job.backoffLimit }}
  activeDeadlineSeconds: {{ .Values.operator.job.activeDeadlineSeconds }}
  ttlSecondsAfterFinished: {{ .Values.operator.job.ttlSecondsAfterFinished }}
  {{- if .Values.operator.job.ignoreDisruptions }}
  podFailurePolicy:
    rules:
      - action: Ignore
        onPodConditions:
          - type: DisruptionTarget
  {{- end }}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: twitch-stream-recorder
        app.kubernetes.io/instance: {{ .Release.Name }}
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      terminationGracePeriodSeconds: {{ .Values.operator.job.terminationGracePeriodSeconds }}
      {{- with .Values.operator.job.priorityClassName }}
      priorityClassName: {{ . }}
      {{- end }}
      containers:
        - name: recorder
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          command: ["python", "-m", "recorder.cli", "record"]
          args: ["--metadata-interval", "{{ .Values.operator.job.metadataInterval }}"]
          env:
            {{- include "twitch-stream-recorder.twitchEnv" . | nindent 12 }}
            {{- include "twitch-stream-recorder.podEnv" . | nindent 12 }}
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
          volumeMounts:
            - name: video-storage
              mountPath: /app/rec
            {{- with .Values.volumeMounts }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
      volumes:
        - name: video-storage
          persistentVolumeClaim:
            claimName: {{ include "twitch-stream-recorder.recordingsClaim" . }}
        {{- with .Values.volumes }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      {{- with .Values.operator.job.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.operator.job.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.operator.job.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
{{- end -}}
