# Amafha Procurement Frontend — Draft

## Product direction

This is a full procurement application, not only a dashboard. The dashboard is the home/overview screen. The core workflows are Suppliers, RFQs, Agent Attention, and Procurement Reports.

## 1. Navigation

- Dashboard
- RFQs
- Suppliers
- Reports
- Agent Attention
- Settings

## 2. Dashboard

Show operational status at a glance:

- Active RFQs
- Quotes received
- Suppliers awaiting response
- Items needing client input
- Recent RFQs
- Recent supplier activity
- Deadline indicators

The dashboard should link directly into the underlying workflow rather than duplicate it.

## 3. Supplier database

### Supplier list

- Search suppliers
- Filter by category
- Filter active/inactive
- Add supplier
- Edit supplier
- Deactivate/remove supplier
- Import CSV

### Supplier model

A supplier can belong to multiple categories. Example:

`Electronics + Hardware`

The existing Supabase `suppliers.category` text array should remain the source for these categories.

### Add/edit supplier

Fields:

- Supplier name
- WhatsApp phone number
- One or more categories
- Notes
- Active/inactive

### CSV import

Flow:

1. Upload CSV
2. Validate columns and values
3. Normalize phone numbers
4. Detect duplicates against existing suppliers
5. Preview valid/invalid/duplicate rows
6. Import valid rows

Supported logical columns:

- name
- phone_number
- category
- notes

A CSV category containing multiple values such as `electronics,hardware` becomes an array.

## 4. RFQ creation

The client does NOT select individual suppliers.

Flow:

1. Create RFQ
2. Select one supplier category
3. Enter product name
4. Enter specifications
5. Enter quantity
6. Set response deadline
7. System finds all active suppliers belonging to that category
8. Show the matched supplier count/list for transparency
9. Client sends the RFQ
10. Backend creates `rfq_suppliers` records and queues outbound WhatsApp messages

Example:

`Category: Electronics`

All active suppliers whose category array contains Electronics receive the RFQ.

If no suppliers match, show a blocking message with a link to manage suppliers.

## 5. WhatsApp sending architecture

Bulk outbound messages must be queue-based. Do not fire all Evolution API requests simultaneously.

Every outbound message type should use the same queue:

- Initial RFQ
- Supplier clarification
- Thank-you
- 50% reminder
- 70% reminder
- 90% reminder
- Final/closing message
- Client-directed supplier response

Conceptual flow:

`Create message job -> queued -> wait according to configured pacing -> send through Evolution API -> log result -> process next job`

The queue should support retries/backoff for transient failures. Pacing should be controlled by backend configuration rather than by the client on each RFQ.

The UI should expose queue status/activity but does not need to expose implementation-level delay controls to normal clients.

## 6. RFQ tracking

RFQ detail should show:

- Product
- Category
- Specifications
- Quantity
- Created time
- Deadline/due time
- Overall status
- Suppliers contacted
- Response status per supplier
- Quotes received
- Clarifications
- Reminder stage/activity
- Ranking/report availability

A client should be able to open an RFQ and understand exactly where it stands without opening WhatsApp.

## 7. Agent attention / human-in-the-loop

The agent should be autonomous by default.

The client should only be involved after the agent has exhausted relevant available context, including:

- Current RFQ
- Supplier's active RFQs
- Current conversation history
- Previous clarification context
- Existing quotes
- Supplier information/history available to the agent

Decision principle:

`Can the meaning be resolved from available data? -> AI resolves it.`

`Can the supplier resolve the missing information? -> AI asks supplier.`

`Is the remaining issue a client business decision or genuinely unresolvable from available data? -> create a client task.`

Do NOT use a simplistic rule such as low model confidence alone to escalate to the client.

### Agent Attention UI

Example:

> **Needs your input**
>
> Al Noor Electronics asked whether the requested item should be the standard or premium model. The RFQ does not specify which option is required.
>
> **What should I tell the supplier?**
>
> `[ Standard ]  [ Premium ]`
>
> `[ Type an instruction ]`

The client's response should go back through the agent. The frontend should not directly construct arbitrary WhatsApp messages.

Conceptual path:

`Client instruction -> backend/agent -> generate appropriate supplier message -> outbound queue -> Evolution API -> supplier`

## 8. Procurement reports

Reports are a first-class workflow.

A client should be able to select an RFQ/product and view a procurement comparison containing:

- Supplier
- Quote price
- Delivery time
- Quality/warranty notes
- Quote completeness
- Supplier response/performance information when available
- AI ranking
- AI reasoning
- Overall recommendation

The client can generate a PDF procurement report.

Do not invent supplier ratings where the database has insufficient evidence. Performance indicators should be based on actual supplier history/data, or clearly marked as unavailable.

## 9. Report layout draft

`LED Panel — 60W`

`Quantity: 20 | Category: Electronics`

### Supplier comparison

| Supplier | Price | Delivery | Quality/Notes |
|---|---:|---|---|
| Supplier A | ... | ... | ... |
| Supplier B | ... | ... | ... |
| Supplier C | ... | ... | ... |

### AI recommendation

- Ranked supplier list
- Best supplier
- Reasoning

### Supplier performance

Use only measurable historical information available to the system. If history is insufficient, display `Insufficient history`.

### Actions

- Generate PDF
- View RFQ
- View supplier

## 10. Current Supabase mapping

Existing tables used by the frontend:

- `clients`
- `suppliers`
- `rfqs`
- `rfq_suppliers`
- `quotes`
- `pending_clarifications`
- `message_log`
- `rfq_rankings`

The RFQ category is now part of the canonical schema and is used to match suppliers by category.

A future client-agent task table should only be added after the exact interaction contract is finalized.

## 11. UX principles

- Procurement-first, not AI-first.
- Show the agent's work without forcing the client to understand technical internals.
- Never make the client select suppliers for an RFQ.
- Make bulk operations observable.
- Make WhatsApp activity auditable.
- Keep client decisions explicit and actionable.
- Avoid fabricated ratings or certainty.
- Every major item should lead to a useful detail page.
