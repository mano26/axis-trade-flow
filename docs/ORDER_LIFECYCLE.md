# Order Lifecycle State Machine

## States

| Status              | Description                                          |
|---------------------|------------------------------------------------------|
| `open`              | Order entered, working, no fills yet                 |
| `cancelled`         | Unfilled order cancelled (terminal)                  |
| `partial_fill`      | Some quantity filled, balance still working           |
| `partial_cancelled` | Partial fill kept, remaining balance cancelled        |
| `filled`            | Full quantity executed                                |
| `amended`           | Fill data modified after execution                   |
| `reported`          | Submitted to exchange, awaiting response             |
| `report_accepted`   | Exchange accepted the trade report (terminal)        |
| `report_failed`     | Exchange rejected the trade report (retryable)       |

## Transitions

```
OPEN ──→ CANCELLED                        (user cancels unfilled order)
OPEN ──→ PARTIAL_FILL                     (partial execution)
OPEN ──→ FILLED                           (full execution)

PARTIAL_FILL ──→ PARTIAL_FILL             (additional fill on same order)
PARTIAL_FILL ──→ FILLED                   (remaining balance filled)
PARTIAL_FILL ──→ PARTIAL_CANCELLED        (remainder cancelled)
PARTIAL_FILL ──→ REPORTED                 (submitted to exchange)

PARTIAL_CANCELLED ──→ REPORTED            (submitted to exchange)
PARTIAL_CANCELLED ──→ AMENDED             (fill data corrected)

FILLED ──→ REPORTED                       (submitted to exchange)
FILLED ──→ AMENDED                        (fill data corrected)

AMENDED ──→ REPORTED                      (resubmitted after correction)

REPORTED ──→ REPORT_ACCEPTED              (exchange accepted)
REPORTED ──→ REPORT_FAILED                (exchange rejected)

REPORT_FAILED ──→ REPORTED                (retry submission)
```

## Modification Rules

- **OPEN orders**: Can be modified in place (same ticket number). The old
  trade string and legs are replaced. Before/after states captured in audit.
- **PARTIAL_FILL orders**: Cannot be modified on the same ticket. To change
  the working balance, finalize the partial fill and create a new ticket
  for the revised remainder.

## Counterparty Allocation Status (per Fill)

| Status                | Description                                    |
|-----------------------|------------------------------------------------|
| `pending_allocation`  | Fill recorded but counterparties not yet entered|
| `allocated`           | All counterparties entered, quantities match    |

Card and ticket generation require all fills to be `allocated`.

## Audit Trail

Every state transition creates an entry in the `audit_log` table with:
- `action`: The type of change (e.g., `order_status_change`)
- `before_value`: JSON snapshot of state before the change
- `after_value`: JSON snapshot of state after the change
- `user_id`: Who performed the action
- `created_at`: When (UTC timestamp)
- `ip_address`: From where
