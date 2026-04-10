# AXIS Trade Flow — SaaS Platform

**Multi-tenant SOFR options trade confirmation, card generation, ticket generation,
and exchange reporting platform.**

Ported from the Excel/VBA AXIS Trade Flow v4 tool to a Python/Flask web application
with PostgreSQL persistence, role-based access control, and Rithmic API integration
for post-trade reporting to CME via Dorman Trading.

---

## Architecture Overview

### Order Lifecycle (State Machine)

```
OPEN ──→ CANCELLED
  │
  ├──→ MODIFIED (same ticket, unfilled only, returns to OPEN)
  │
  ├──→ PARTIAL_FILL
  │         │
  │         ├──→ PARTIAL_FILL (additional fills accumulate)
  │         ├──→ FILLED
  │         └──→ PARTIAL_CANCELLED (remainder killed, fills kept)
  │
  └──→ FILLED

Post-fill (once counterparties allocated, cards/tickets generated):

FILLED / PARTIAL_FILL / PARTIAL_CANCELLED
  └──→ REPORTED ──→ REPORT_ACCEPTED
                └──→ REPORT_FAILED

AMENDED can apply to any filled state (full audit trail).
```

### Counterparty Allocation Status (per Fill)

```
PENDING_ALLOCATION ──→ ALLOCATED
```

### Key Business Rules

1. **Proportional fills**: All legs fill in proportion. A fill is a quantity at
   the order level; every leg scales by the same ratio.
2. **Ticket persistence on partials**: Ticket number stays the same across
   partial fills. If the customer wants the partial sent to exchange, a new
   ticket is manually created for the remainder.
3. **Modify vs. reticket**: Unfilled orders can be modified in place (same ticket).
   Partially filled orders that need the balance modified must finalize the partial
   and create a new ticket.
4. **Price reconciliation**: Hard block on save. Leg prices must net to package
   premium within floating-point tolerance. Prices are editable after save with
   full audit trail.
5. **Card/ticket generation blocked** until counterparty allocation is complete.
6. **Rithmic integration**: Post-trade reporting only (not live order routing).
   OTC/voice fills are reported to CME as ex-pit/block trades.

---

## Tech Stack

| Layer          | Technology                          |
|----------------|-------------------------------------|
| Framework      | Flask 3.x                           |
| Database       | PostgreSQL 15+                      |
| ORM            | SQLAlchemy 2.x + Alembic migrations |
| Auth           | Flask-Login + Werkzeug              |
| Templates      | Jinja2                              |
| CSS            | Custom (no framework)               |
| Hosting        | Railway                             |
| Exchange API   | Rithmic Protocol Buffer API (stub)  |

---

## Project Structure

```
axis-trade-flow/
├── app/
│   ├── __init__.py              # Flask app factory
│   ├── config.py                # Environment-based configuration
│   ├── extensions.py            # SQLAlchemy, Login Manager, etc.
│   ├── models/
│   │   ├── __init__.py          # Model registry
│   │   ├── tenant.py            # Tenant (firm) model
│   │   ├── user.py              # User model with roles
│   │   ├── order.py             # Order + OrderLeg models
│   │   ├── fill.py              # Fill + FillCounterparty models
│   │   ├── print_event.py       # Print audit trail
│   │   └── audit.py             # Generic audit log
│   ├── routes/
│   │   ├── __init__.py          # Blueprint registration
│   │   ├── auth.py              # Login/logout/registration
│   │   ├── orders.py            # Order CRUD + lifecycle
│   │   ├── fills.py             # Fill entry + counterparty allocation
│   │   ├── cards.py             # Card generation + print
│   │   ├── tickets.py           # Ticket generation + print
│   │   ├── reports.py           # Order log + EOD reconciliation
│   │   ├── exchange.py          # Rithmic submission endpoints
│   │   └── admin.py             # User/tenant management
│   ├── services/
│   │   ├── __init__.py
│   │   ├── trade_parser.py      # Trade string → parsed legs
│   │   ├── strategy_handlers.py # Strategy-specific leg builders
│   │   ├── contract_map.py      # SOFR contract code mapping
│   │   ├── validation.py        # Price reconciliation + field checks
│   │   ├── card_generator.py    # HTML card rendering
│   │   ├── ticket_generator.py  # HTML ticket rendering
│   │   ├── rithmic_client.py    # Rithmic API client (STUBBED)
│   │   └── audit_service.py     # Audit trail writer
│   ├── templates/               # Jinja2 templates
│   │   ├── layouts/base.html
│   │   ├── auth/login.html
│   │   ├── orders/...
│   │   ├── cards/...
│   │   ├── tickets/...
│   │   └── reports/...
│   └── static/
│       ├── css/main.css
│       ├── js/app.js
│       └── img/
├── migrations/                  # Alembic migrations
│   ├── env.py
│   ├── alembic.ini
│   └── versions/
├── tests/
│   ├── __init__.py
│   ├── test_trade_parser.py
│   ├── test_strategy_handlers.py
│   ├── test_validation.py
│   └── test_order_lifecycle.py
├── scripts/
│   └── seed_dev_data.py         # Development seed data
├── docs/
│   ├── TRADE_STRING_SYNTAX.md   # Trade input format reference
│   ├── ORDER_LIFECYCLE.md       # State machine documentation
│   └── RITHMIC_INTEGRATION.md   # Exchange reporting notes
├── .env.example
├── .gitignore
├── Procfile                     # Railway process definition
├── railway.toml                 # Railway configuration
├── requirements.txt
├── wsgi.py                      # WSGI entrypoint
└── README.md
```

---

## Development Setup

```bash
# Clone and install
git clone <repo-url>
cd axis-trade-flow
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your database URL, secret key, etc.

# Initialize database
flask db upgrade

# Seed development data
python scripts/seed_dev_data.py

# Run locally
flask run --debug
```

---

## Railway Deployment

```bash
# Railway CLI
railway login
railway init
railway link
railway up
```

The `Procfile` and `railway.toml` are pre-configured for gunicorn with
PostgreSQL addon.

---

## Regulatory Notes

This application handles financial trade data subject to CME Group exchange
rules and CFTC regulations. All state transitions, price modifications,
counterparty changes, and print events are logged to an immutable audit trail.
Records are never hard-deleted; soft-delete with `deleted_at` timestamps is
used throughout. See `docs/ORDER_LIFECYCLE.md` for the complete state machine
specification.
