# Capture outcomes and reconciliation contract

This correction is based on commit `c7d01aea3fc9196a9d04fc2837f1c1a170913c66`.
It has been validated with offline fixtures, not a real PayPal/LLM service.

## What constitutes completed payment

Only one explicitly `COMPLETED` capture for the expected order with a nonempty string capture ID and
the expected share amount and currency counts toward `captured_cents` and
`captured_count`. An order-level `COMPLETED`, HTTP 200/201 alone, missing capture
data, multiple capture records, or mismatched amounts/currencies is insufficient.
The client keeps order status separate from capture status.

Valid scalar provider identity is preserved as returned: a missing ID is never
filled from the request. Wire amounts must be exact decimal **strings** in a supported
two-decimal currency, not JSON numbers, currency-suffixed input or amounts that
would need rounding/truncation. Valid strings such as `100`, `100.0`, `100.00`
and `.50` remain supported. Malformed purchase-unit/payment/capture containers
are recorded as validation errors, never silently dropped. They hold even an
otherwise `APPROVED` order for reconciliation instead of enabling capture.
See the [PayPal wire-money definition](https://developer.paypal.com/api/orders/v2/definitions/money).

`PENDING` becomes `capture_pending`; `DECLINED`, `FAILED`, `DENIED`, or a refunded
capture becomes `capture_failed`; missing/unrecognized/inconsistent data becomes
`capture_unknown`. These states contribute zero to completed-capture totals.
This does not add refund support: refunded evidence is conservatively excluded
and requires review. See PayPal's [capture definition](https://developer.paypal.com/api/orders/v2/definitions/purchase_unit).

## Compatibility

- `collected_cents` historically means **the requested collection target**, not
  money received. Its existing calculation and meaning are preserved.
- `collection_target_cents` is an additive, explicitly named alias. The UI uses
  it for “Collecting” and progress, falling back to the old field for old-shaped
  API responses. Expense totals, debt amounts, credits and order payloads do not
  change. `captured_cents` is the completed-payment aggregate.
- Share responses add `capture_status`, `provider_order_status`,
  `capture_attempted`, `reconciliation_required`, `capture_evidence`, and
  `capture_history`, plus `capture_validation_errors`. Evidence contains capture
  identifiers, statuses, amount values and currencies, not payer personal details.
  Invalid scalar values and structural error descriptions are retained for
  review without treating them as verified money. Nonfinite numbers are stored
  as typed objects, e.g. `{"invalid_type":"nonfinite_number","value":"NaN"}`,
  not invalid JSON constants or strings usable as capture IDs/amounts.
  Objects/arrays in scalar fields retain only an `invalid_type` marker; arbitrary
  nested provider content is not copied into event responses/history. Missing or
  non-string order status also requires reconciliation. These rules apply before
  storage, so later successful Sync does not leave browser-invalid history.
  History retains
  changed observations and uncertain request outcomes for manual reconciliation.
- Single capture continues to return HTTP 200 when a provider outcome can be
  represented, but callers must inspect `share.status`; 200 does not imply paid.
  Transport/API errors retain their existing error response while the share is
  held as unknown. A bulk response adds `unresolved`; `captured` contains only
  completed names. A transport failure may appear in both `failed` (the request)
  and `unresolved` (the money outcome).
- Existing in-memory records do not migrate across restarts: the application has
  no persistence. This patch does not add restart-safe reconciliation/storage.

## No blind repeat payment actions

The service latches a capture attempt before issuing its provider request. A
pending, failed, malformed or lost response never unlocks another capture or an
order retry. Repeated single/bulk calls are read-only with respect to held shares.
Concurrent calls are serialized by the existing service lock. A definite
`ORDER_NOT_APPROVED` rejection is the exception: a later explicit user action
after buyer approval remains allowed. No timed or automatic retries are added.

Use **Sync** to read the existing order and reconcile it. Sync can confirm a later
completed capture without issuing another capture request. A missing subsequent
capture representation does not erase prior identifiers or unlock another payment.
An unresolved state may require manual provider-account review; there is no
automatic “clear hold,” replacement order, refund or retry path in this patch.
Do not restart/recreate an event to bypass an unresolved payment.

The UI removes checkout/copy/capture/retry/payment-reminder actions for unresolved
shares and never announces success for them. The backend also blocks unresolved
payment reminders. Repeated return visits reconcile held outcomes; `/cancel`
with a token never requests capture. Existing completed thank-you reminders remain.

## Offline checks

```bash
python3 -m unittest discover -s tests -t . -v
bash scripts/smoke.sh
node --test tests/test_capture_ui.mjs   # Optional dev-only UI logic tests; Node built-ins
node --check static/app.js
```

The Python regressions exercise real local HTTP clients with fake providers,
including negative capture statuses, amount/currency mismatches, malformed and
multiple captures, lost responses, concurrent repeats, sync reconciliation,
bulk accounting and reminder suppression. The Node suite executes browser
logic using an in-memory HTTP/DOM fixture; it is not browser visual QA.

Real sandbox/model compatibility, actual buyer redirects, visual browser QA and
production durability remain separate verification gates.
