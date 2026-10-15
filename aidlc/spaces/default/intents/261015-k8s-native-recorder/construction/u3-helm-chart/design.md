# U3 Helm chart: infrastructure design, code summary

## Files
| File | Change |
|---|---|
| `helm/crds/twitchchannels.recorder.civon.github.io.yaml` | new CRD (status subresource, printer columns, defaults, validation) |
| `helm/templates/controller.yaml` | ServiceAccount, Role, RoleBinding, Job template ConfigMap, controller Deployment (only with `operator.enabled`) |
| `helm/templates/twitchchannels.yaml` | one TwitchChannel per `channels` entry (only with `operator.enabled`) |
| `helm/templates/recordings-pvc.yaml` | shared claim `twitch-rec-storage`, `helm.sh/resource-policy: keep` (unless `storage.existingClaim`) |
| `helm/templates/_helpers.tpl` | `secretName`, `recordingsClaim`, `twitchEnv`, `podEnv`, `jobTemplate` |
| `helm/templates/deployment.yaml`, `pvc.yaml` | wrapped in `if not operator.enabled`; secret name via helper |
| `helm/templates/secret.yaml` | skipped when `secrets.existingSecret` is set |
| `helm/values.yaml` | `operator.*`, `events.sinks`, `secrets.existingSecret`, `storage.existingClaim/sharedSize/sharedAccessMode`, per-channel options documented |

## Job template choices
| Setting | Value | Why |
|---|---|---|
| `restartPolicy` | Never | required by `podFailurePolicy`; retries are new pods |
| `podFailurePolicy` | Ignore `DisruptionTarget` | node drains and preemption do not use up `backoffLimit` (Kubernetes 1.26+) |
| `backoffLimit` | 3 | real failures only |
| `terminationGracePeriodSeconds` | 300 | time to remux after SIGTERM |
| `activeDeadlineSeconds` | 172800 | safety net for a stuck capture |
| `ttlSecondsAfterFinished` | 86400 | `kubectl get jobs` keeps a day of history |
| `automountServiceAccountToken` | false | recording pods never call the Kubernetes API |
| downward API env | `POD_NAME`, `POD_NAMESPACE`, `NODE_NAME`, `APP_VERSION`, `EVENT_SINKS` | context in every wide event |

## Storage
Recording Jobs for all channels share one claim, so a ReadWriteMany class
(CephFS) lets them run on any node. With `local-path` (RWO) set
`storage.sharedAccessMode: ReadWriteOnce` and pin Jobs with
`operator.job.nodeSelector`. Which repository owns a CephFS StorageClass for
k3s (cluster-terraform or homeserver-ansible) is ACC-133's question; nothing in
cluster-terraform needs to change for this PR.

## CRD lifecycle
Helm installs `crds/` on first install only. After a CRD change run
`kubectl apply -f helm/crds/` (documented in the README and the CRD file).
