# Step 3: Business Rules Specification

*Northbridge Community Clinics is a fictional demo organization. All policies, including the expiration window, tolerance, reminder, and retention settings, are fictional demo policies.*

**Phase key:** **P1** = local inventory (receiving, issuing, imports, counts, expiration, low-stock views, exports). **P2** = transfers, holds, disposal and approvals, enforced roles, audit-history views. P2 rules are designed now and built later.

Locations: Riverside Family Clinic, Hillcrest Health Center, Eastgate Walk-In Clinic, Central Supply Room.

## 3.1 Quantities and Units

- **Base unit.** Each product has one base unit. All balances and movements are stored in base units. *(P1)*
- **Package conversions.** A product may define a purchase unit with a whole-number factor to base units (for example, 1 box = 100 each). The factor is copied onto each receipt line, so later catalog edits do not change history. Whole-number purchase quantities are accepted. Partially opened packages are entered in base units. *(P1)*
- **Whole numbers only.** All quantities are integers. Fractions are rejected. *(P1)*
- **Quantity effects.**
  - Receipt: on-hand increases. *(P1)*
  - Issue: on-hand decreases. *(P1)*
  - Discard: on-hand decreases. *(P2)*
  - Count adjustment: on-hand changes by the automatic or approved variance. *(P1)*
  - Dispatch: source on-hand decreases and in-transit increases. *(P2)*
  - Transfer receipt: in-transit decreases and destination on-hand increases. *(P2)*
  - Hold or release: held quantity changes; on-hand does not. *(P2)*
- **Zero and negative quantities.** Zero and negative quantities are rejected for receipts, issues, dispatches, holds, and discards. Count lines accept a physical quantity of zero regardless of expected quantity. A zero variance creates no stock movement. *(P1)*
- **Movements only for quantity changes.** Only actions that change stock quantities create movements. Completing a zero-variance count, cancelling a transfer, and recording approval decisions create no movements. The resulting release, discard, or adjustment creates movements when it is applied. *(P1 and P2 as listed)*

**Test cases:** 2 boxes at 100 per box creates 200 base units. 1.5 boxes is rejected. A receipt of 0 is rejected. A count of 0 against 5 expected is valid and produces a variance of −5. A count of 0 against 0 expected is valid, with zero variance and no movement.

## 3.2 Lot Identity

- A lot is identified by **product + lot number**. Lot numbers are unique within a product, not across products. *(P1)*
- Each lot has **one expiration date, or null when the product permits it.** The date is stored without a time. *(P1)*
- Expiration requirement is configured per product from its sample labeling. No category-wide exemption applies. *(P1)*
- A receipt that repeats an existing product-lot with a different expiration date is rejected for review. The system never changes an existing lot's expiration silently. *(P1)*
- Because a lot has one expiration date, all units of that lot in one storage area share the same expiration status. *(P1)*

**Test cases:** the same product and lot received twice with the same date is accepted and sums. The same lot with a different date is rejected. The same lot number under two products creates two lots. A product requiring expiration rejects a receipt with no date.

## 3.3 Expiration Rules (Fictional Demo Policy)

- **Timezone.** Comparisons use **America/New_York**. The evaluation date is the local calendar date at the moment of evaluation. *(P1)*
- **Expired.** A lot is expired when the local evaluation date is on or after its expiration date. *(P1)*
- **Expiring soon.** A lot is expiring soon when it is not expired and its expiration date falls within the next **60 days** (default, configurable). *(P1)*
- **Effect on availability.** An expired lot contributes zero available quantity at every storage area where it sits. Expiring-soon stock remains available. *(P1)*
- **Classifications are separate.** Expired and expiring-soon are reported as distinct categories. *(P1)*
- **Disposal of expired stock** is a P2 action, approved by a manager. In P1, expired stock stays on-hand and unavailable. *(P1 unavailability; P2 disposal)*

**Test cases:** expiration = today → expired. Expiration = yesterday → expired. Expiration = today + 60 → expiring soon. Expiration = today + 61 → neither. Null expiration → neither.

## 3.4 Submission Keys and Transaction References

- Every mutating request carries a **client-generated submission key** (UUID, required). *(P1)*
- **Same key, same content:** returns the original result without changing data.
- **Same key, different content:** returns **409 Conflict** and changes nothing.
- **Authorization is checked before a saved result is returned.** A user who has lost access receives 403 and no prior result. *(P1 with the fictional actor; P2 with enforced roles)*
- **Atomicity.** The submission key, the transaction record, and all resulting movements are written in one database transaction. A failed request leaves none of them. *(P1)*
- **Concurrency.** When two requests with the same key arrive simultaneously, exactly one is processed. The other waits and receives the same result, or receives 409 if the contents differ. Never two results. *(P1)*
- **Retention.** Keys are kept for the lifetime of the demo database. A deliberate demo reset may remove them. *(P1)*
- **Packing-slip reference** is an optional text field, never used for identity. Duplicate slip numbers produce a warning, not a rejection. *(P1)*

**Test cases:** a double-submitted receipt produces one receipt. The same key with a changed quantity returns 409. Two receipts with the same slip and different keys are both accepted, with a warning. Two simultaneous identical requests produce one set of movements. A retry by a user who has lost access returns 403.

## 3.5 Count Locking and Lifecycle

- **Starting a count** locks the storage area. Only one open count may exist per storage area. *(P1)*
- **While locked**, receipts, issues, and adjustments affecting that area are rejected, naming the active count. *(P1)*
- **Safety-hold exception.** An authorized staff member may place an immediate safety hold during an open count. It is recorded as a count event and must be acknowledged before completion. *(P2)*
- **Submitting the physical count** records the counted quantity per line and computes variance per line. *(P1)*
- **Line states after submission:**
  - *Within tolerance:* ready for automatic adjustment. *(P1)*
  - *Out of tolerance:* blocks completion until resolved by recount or cancellation of the count. In P2, a manager may approve the proposed adjustment instead. *(P1 blocking; P2 approval)*
- **Rejected or unapproved discrepancies** are never dismissed silently. In P2, rejecting a proposed adjustment requires a recount or a documented resolution before the count can close. A rejection alone does not clear the line. *(P2)*
- **Recounts.** A recount clears an out-of-tolerance line only if the revised count is within tolerance. Otherwise the line remains unresolved. *(P1)*
- **Completion** is allowed only when every line is resolved. Completion applies within-tolerance adjustments (and approved adjustments in P2) and releases the lock in one transaction. Count-generated adjustments are the only changes permitted through the lock. *(P1)*
- **Cancellation** before completion applies no count adjustments and releases the lock. *(P1)*
- **Reminder.** A count open longer than **24 hours** is flagged for review. The flag does not complete the count or unlock the area. *(P1)*
- **Held-quantity invariant during completion.** Count completion must preserve **0 ≤ held_quantity ≤ on_hand_quantity** for every balance. A discrepancy that would reduce on-hand below held quantity requires explicit reconciliation of the affected hold before completion. *(P2 for holds; the invariant is enforced in the P1 schema, where held quantity is always zero)*

**Test cases:** an issue during an open count is rejected. Completion is blocked while an out-of-tolerance line is unresolved. A recount clears an out-of-tolerance line only if the recount is within tolerance: expected 100, first count 97, recount 98 clears; recount 97 does not. Cancellation applies nothing and releases the lock. A count open 25 hours is flagged and stays open. A count that would drop on-hand below held quantity cannot complete until the hold is reconciled.

## 3.6 Tolerance and Approval

- **Automatic tolerance:** a count line is within tolerance when the absolute integer variance is at most the **greater of 2 base units or 2% of expected quantity**, rounded down. *(P1)*
- **Within-tolerance adjustments** are automatic. Their source is the count, and they carry no approver. *(P1)*
- **Out-of-tolerance count variance** blocks completion in P1. In P2, it requires manager approval. Approval only decides the adjustment; all count adjustments are applied together at completion, and cancellation applies none. *(P1 block; P2 approval)*
- **Manual adjustments, disposal, and hold release** require manager approval. *(P2)*
- **Value thresholds** are deferred. Cost data is out of scope. *(P1)*

**Test cases:** expected 100, counted 98 → variance 2, threshold 2, within tolerance. Expected 100, counted 97 → variance 3, threshold 2, out of tolerance. Expected 0, counted 2 → threshold max(2, 0) = 2, variance 2, within tolerance. Expected 0, counted 3 → variance 3, out of tolerance.

## 3.7 Availability and Holds

- **Available quantity** for a balance is:
  - **0** if the lot is expired;
  - otherwise **on_hand − held**, floored at zero.
- Because a lot has one expiration date, expired stock is wholly unavailable regardless of held quantity. Held and expired quantities never need to be subtracted twice. *(P1 rule; held values P2)*
- **Examples:**

  | Lot balance | Available |
  |---|---|
  | 10 on-hand, unexpired, 4 held | 10 − 4 = 6 |
  | 10 on-hand, expired, 4 held | 10 → 0 available |
  | 10 on-hand, no expiration required, 4 held | 10 − 4 = 6 |

- **Holds** apply to quantity physically at a storage area. A new hold may cover only unheld quantity. Expired stock may be held. *(P2)*
- **In-transit stock** is never counted in any location's available quantity. *(P2)*
- **Hold release** reduces held quantity. The stock becomes available only if it is still unexpired and meets all other eligibility rules at execution time. Otherwise it stays on-hand and unavailable, and the requester is told why. *(P2)*

**Test cases:** the three examples above. With 10 on-hand and 4 held, an additional hold of 6 succeeds; a hold of 7 is rejected. Release of held stock whose lot has since expired leaves it on-hand and unavailable.

## 3.8 Transfers *(P2)*

- **Who may dispatch:** any authorized sending staff member at the source location.
- **Dispatch** requires sufficient available stock at the source for every line. Insufficient stock rejects the whole dispatch.
- **Partial receipt** leaves the unreceived remainder in transit. The remainder is resolved only by:
  - receipt of the remaining quantity at the destination, or
  - an approved **loss write-off** (manager approval), or
  - an **internal return** to the sending location.
- **Internal return.** A return is a linked transfer that references the original transfer line. Its dispatch consumes the remainder from in-transit, not from the sender's on-hand, so on-hand is never deducted twice. Its receipt at the sender increases sender on-hand. The return has its own movements and receipt. No supplier return is involved.
- **Over-receipt** is rejected.
- **A transfer cannot close** while any in-transit quantity is unresolved.
- **Cancellation** before dispatch closes the transfer with no movements. After dispatch, an internal return is used instead.

**Test cases:** dispatch 10, receive 7 → 3 in transit, transfer open. Internal-return dispatch of the 3 → original line in-transit 0, return line in-transit 3, sender on-hand unchanged. Return receipt of the 3 → sender on-hand increases by 3 exactly once. Over-receipt of 11 against 10 → rejected. Dispatch of 6 with 5 available → rejected, no movement.

## 3.9 Reorder Thresholds

- A **threshold** is set per **product and location**, in base units. *(P1)*
- A product at a location is **low** when its available quantity is at or below its threshold. *(P1)*
- Lots are not checked individually. *(P1)*
- A product with no threshold at a location is not flagged there. *(P1)*

**Test cases:** threshold 20, available 20 → low. Available 21 → not low. Threshold 0 flags only when available is 0. An expired lot excluded from availability can make a product low.

## Settings Summary

| Setting | Default | Phase |
|---|---|---|
| Expiring-soon window | 60 days (configurable) | P1 |
| Tolerance | greater of 2 base units or 2% of expected, rounded down | P1 |
| Open-count reminder | 24 hours (flag only) | P1 |
| Submission key retention | Lifetime of demo database | P1 |
| Expiration requirement | Per product | P1 |
| Timezone | America/New_York | P1 |
