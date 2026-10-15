# Audit trail

| Time (UTC) | Event | Detail |
|---|---|---|
| 2026-10-15T09:00Z | REQUEST | "Propose arch that fit new k8s native design on linear, draft code and create PR using aidlc skill. Aware of metadata change like category change from singing to game, export as wide event or log." |
| 2026-10-15T09:01Z | CONTEXT | Linear project P-ACC-1 "Twitch Recorder Platform" and backlog ACC-15, 16, 17, 18, 19, 20, 76; related ACC-114 (event types incl. category change), ACC-34 (observability topology), ACC-141 (chat to data lake), ACC-165 (Loki). No existing Linear document for a k8s-native design. |
| 2026-10-15T09:02Z | SKILL | No AI-DLC skill installed in the session. Used the upstream awslabs/aidlc-workflows 2.x stage definitions as the method; scope `classic` (Inception + Construction to Build and Test). |
| 2026-10-15T09:03Z | DEVIATION | Ran every stage in one pass without per-stage human gates; the draft PR is the single approval gate. Recorded in aidlc-state.md. |
| 2026-10-15T09:03Z | DEVIATION | Reverse Engineering produced one consolidated file instead of nine codekb artifacts: the codebase is one 200-line script plus a Helm scaffold. |
| 2026-10-15T09:05Z | DECISION | ADR-001: controller + one Job per live broadcast (over Deployment-per-channel and over MQ + KEDA). |
| 2026-10-15T09:05Z | DECISION | ADR-002: metadata changes detected by Helix polling inside the recording Job; EventSub later. |
| 2026-10-15T09:05Z | DECISION | ADR-003: wide events as JSON lines on stdout plus a per-recording `events.jsonl` sidecar; logfmt `log` sink as the plain-log option. |
| 2026-10-15T09:05Z | DECISION | ADR-004: plain reconcile loop over the Kubernetes REST API in Python, no controller framework. |
| 2026-10-15T09:05Z | DECISION | ADR-005: API group `recorder.civon.github.io`, one CRD (`TwitchChannel`); no `Recording` CRD yet. |
| 2026-10-15T09:20Z | LOOPBACK | k3s run showed a gap: streamlink exiting cleanly mid-broadcast would complete the Job and the controller would not re-record that stream id. `record` now records further parts in the same Job while the same broadcast is live. Requirements FR-3 updated. |
| 2026-10-15T09:26Z | REVIEW | Self-review fixes: SIGTERM between two parts is honoured; no back-off sleep after a stop; later parts get a ` - partN` file name so two parts started in the same second cannot overwrite each other. |
| 2026-10-15T09:27Z | VERIFY | 40 unit tests pass on Python 3.8, 3.10, 3.13; real ffmpeg 7.1 remux with chapters; record CLI smoke incl. SIGTERM; legacy chart render byte-identical; operator render validated by kubeconform and applied to k3s v1.31; reconciler run against k3s. See construction/build-and-test/build-and-test.md. |
| 2026-10-15T09:28Z | GATE | Waiting for human approval on the draft PR. |
