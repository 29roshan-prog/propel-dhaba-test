# Input schema — the real `dhaba_tickets.json` shape

> Documents the **structure** of the input, confirmed by inspecting the real
> file. Contains no ticket content. The real data file is gitignored.

```jsonc
{
  "product": "string",
  "note": "string",            // human note about the dataset
  "tickets": [
    {
      "id": "string",                        // e.g. "T-1001" — the idempotency key
      "received_at": "ISO-8601 with +05:30",
      "subject": "string",
      "body": "string",                      // free text; may contain PII and/or injection
      "purchases": [
        {
          "id": "string",                    // e.g. "pay_A1"
          "type": "trial | renewal",         // what kind of purchase
          "amount_inr": 1 | 249 | 1499,      // integer rupees
          "status": "initiated | failed | successful",
          "at": "ISO-8601 with +05:30"
        }
      ],
      "app_opens_since_renewal": 0            // integer usage signal
    }
  ]
}
```

## Fields that drive the money gate
- **`purchases[].status`** — only `successful` is evidence of a real charge.
  `initiated` and `failed` are NOT. This single field prevents the two biggest
  traps in the dataset (T-1005 initiated, T-1009 failed+successful).
- **`purchases[].amount_inr`** — caps any refund. We never recommend more than a
  verified successful charge.
- **`purchases[].type`** — `trial` (₹1) vs `renewal` (₹249/₹1499).

## Where the PII lives
There are **no** structured email/phone/GSTIN fields. The only identifying data
appears **inside `body`** (e.g. a GSTIN, a family reference). Therefore the
privacy rule is enforced by **never logging `subject` or `body`**, not by
dropping a named field.

## Note on `type` vs `status`
The PDF prose says purchases are "in one of four states: initiated, failed,
successful, renewal." In the real JSON these are split across two fields:
`type` ∈ {trial, renewal} and `status` ∈ {initiated, failed, successful}.
"renewal" is a `type`, not a `status`. We model them as the data actually is,
and the enums reflect every value seen in the file plus the ones the PDF names.
