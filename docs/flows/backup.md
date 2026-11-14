# Backup ZIP + merge (no accounts, no server)

```mermaid
flowchart LR
  E[export: VACUUM INTO + sidecars + covers<br/>no audio, age default<br/>preflight + tmp+rename + verify] --> Z[backup.zip + sha256 manifest]
  Z --> S[staging import + verify manifest]
  S --> M[field-level LWW by updated_at<br/>voice-map per-key, pron-dict record union<br/>fingerprint + aliases join]
  M --> C[clamp progress on text_hash change + orphan flag]
  M --> V[sidecar schema version + lazy re-render]
  M --> W[sync prune watermark + tombstones]
```

- Quiesce export during synth (sweep zombie RUNNING + half sidecars).
- Double-import idempotent (content-hash dedupe); forward-migration fixture
  in CI; restore onto older refuses with message; clock-skew warns (never blocks).
- Passphrase rotation affects new backups only.
- Full-disk export never kills last good backup.
