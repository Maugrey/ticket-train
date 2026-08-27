# Model-routing history

This file preserves superseded routing values so a later train audit can
compare or restore a known policy without reconstructing it from prose. It is
historical only; active runs must use [model-routing.md](model-routing.md) and
the matching constants in `scripts/train_controller.py`.

## Policy `2026-08-23-v1`

Superseded on 2026-08-27 by `2026-08-27-v2`.

Legend: `TM` = Terra/Medium, `TH` = Terra/High, `SH` = Sol/High,
`SX` = Sol/XHigh, `SMax` = Sol/Max.

### Analysis

| Criticality ↓ / Complexity → | LOW | MEDIUM | HIGH | MAXIMUM |
|---|---:|---:|---:|---:|
| LOW | SH | SH | SX | SX |
| NORMAL | SH | SH | SX | SX |
| HIGH | SH | SH | SX | SX |
| CRITICAL | SX | SX | SX | SMax |

### Implementation and remediation

| Criticality ↓ / Complexity → | LOW | MEDIUM | HIGH | MAXIMUM |
|---|---:|---:|---:|---:|
| LOW | TM | TH | SH | SX |
| NORMAL | TH | SH | SH | SX |
| HIGH | SH | SH | SH | SX |
| CRITICAL | SH | SH | SX | SX |

Acceptance-test authoring reused this matrix and raised `TM` to `TH` as a
global minimum.

### Initial review

| Criticality ↓ / Complexity → | LOW | MEDIUM | HIGH | MAXIMUM |
|---|---:|---:|---:|---:|
| LOW | TH | SH | SH | SX |
| NORMAL | SH | SH | SH | SX |
| HIGH | SH | SX | SX | SX |
| CRITICAL | SX | SX | SX | SMax |

### Focused follow-up review

| Criticality ↓ / Verification complexity → | LOW | MEDIUM | HIGH | MAXIMUM |
|---|---:|---:|---:|---:|
| LOW | TH | SH | SH | SX |
| NORMAL | SH | SH | SH | SX |
| HIGH | SH | SH | SX | SX |
| CRITICAL | SH | SX | SX | SMax |

### Other superseded rules

- Normal orchestrator: Terra/High.
- Batch triage: always Terra/High.
- Full technical analysis: always Sol.
- Final-train review: highest ticket-review setting was an absolute floor.
- Remediation criticality: initial analysis criticality.
- Settings were compared through the global order
  `TM < TH < SH < SX < SMax < Sol/Ultra`.
- Ultra was treated as a reasoning level above Max.

The Git commit immediately before activation is `8639137`, which remains the
authoritative executable snapshot of this policy.

## Rollback procedure

Do not silently change an active policy after a disappointing run. First audit
the failures against phase, classification, selected route, oracle quality,
escaped defects, tokens, latency, and remediation cycles. If the user decides
to restore a superseded calibration:

1. copy the relevant matrices and phase rules from this history into the
   controller and active routing reference;
2. assign a new policy version instead of reusing an old version identifier;
3. update the routing snapshot tests and controller schema projection;
4. retain both the superseded and restored entries here with activation dates
   and the exact pre-activation Git commit;
5. activate the restored calibration directly only after the user requests it.
