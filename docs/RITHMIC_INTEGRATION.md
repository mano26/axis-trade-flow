# Rithmic Integration — Exchange Reporting

## Overview

AXIS Trade Flow uses Rithmic's Protocol Buffer API to report OTC/voice-brokered
SOFR options trades to CME via Dorman Trading (FCM).

**This is NOT live order routing.** Orders are filled OTC/voice and then
reported to the exchange after the fact as ex-pit or block trade reports.

## Current Status: STUBBED

The Rithmic client (`app/services/rithmic_client.py`) is fully stubbed.
All methods log their intended actions and return simulated responses.

## Activation Checklist

1. **Uncomment dependencies** in `requirements.txt`:
   - `protobuf>=5.29.0`
   - `grpcio>=1.68.0`

2. **Set environment variables** (Railway dashboard or `.env`):
   - `RITHMIC_URI` — gRPC endpoint
   - `RITHMIC_USER` — API username
   - `RITHMIC_PASSWORD` — API password
   - `RITHMIC_SYSTEM_NAME` — "Dorman Trading" or as assigned
   - `RITHMIC_GATEWAY` — "Chicago" or as assigned

3. **Implement `RithmicClient` methods**:
   - `connect()` — Establish gRPC channel, authenticate
   - `submit_trade_report()` — Construct and send Protocol Buffer message
   - `check_submission_status()` — Query exchange response
   - `disconnect()` — Clean shutdown

4. **Message Construction**:
   The submission message must include:
   - Account identifier (Dorman account)
   - Clearing firm identifier
   - Trade date and time
   - For each leg: side, quantity, contract, expiry, strike, option type, price
   - Counterparty information
   - Trade type (ex-pit, block, etc.)

## Response Handling

CME returns an acceptance or rejection for each submission:

- **Accepted**: Order transitions to `REPORT_ACCEPTED`. Reference ID stored.
- **Rejected**: Order transitions to `REPORT_FAILED`. Error message logged.
  User can retry after correcting issues.

## Audit Trail

All submission attempts are logged in the `audit_log` table with:
- The full submission payload (in `after_value`)
- The exchange response (success/failure, reference ID, error message)
- User and timestamp

## Dorman Trading Contact

For Rithmic API credentials, gateway configuration, and account setup,
contact Dorman Trading's technology desk.
