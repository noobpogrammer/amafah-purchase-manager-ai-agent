# Main AI and supplier conversation update

Implemented locally on 7 November 2026. No Jev integration, model change, database migration or production deployment is included.

## Behavior changes

- Known active suppliers with current buying activity can send short replies such as `24`, `Agreed` or mixed-language answers to the main AI in their existing OpenClaw conversation. Empty messages, explicit greetings/personal-only patterns, automation pause and missing buying context retain their gate behavior. For other noncommercial text, the main AI must identify unrelated messages and return `NO_REPLY`; engagement is permission to interpret, not acceptance authority.
- Activation returns current product details, quantities, buyer limits and negotiation/clarification state. The plugin injects these facts into the existing turn instead of instructing the model to fetch the same conversation again.
- `/openclaw/supplier-context` defaults `history_limit` to `0`, returns an empty `conversation_history`, and identifies `history_source` as `openclaw_session`. An explicit limit of 1–50 fetches database recovery history.
- `/openclaw/resolve-rfq-context` defaults `include_history` to `false`. Set it to `true` to recover the per-RFQ conversation snippets. Current quote records, direct invitation evidence and eligible RFQs remain available.
- The plugin now exposes `procurement_validate_rfq_mapping` consistently in its registration, manifest and engaged-turn tool list. No new model call is introduced by these changes.
- Instructions require each product's RFQ, price, availability and conditions to stay together. Mixed price/unavailability messages use separate existing actions; fully priced multi-RFQ batches retain the atomic processing tool. This is a model instruction plus correct tool execution support, not proof that all natural-language interpretations are correct.
- Supplier phone lookup checks all active records within the business, including differently formatted numbers even if one exact match exists. Useful formatting and sufficiently long suffix matches remain, but several possible supplier records raise `AMBIGUOUS_SUPPLIER_PHONE` through the bridge with HTTP 409. No supplier records are automatically merged or deleted. Results are paged to avoid overlooking a conflict after the first page.
- Explicit supplier-ID lookup now applies active/nondeleted filters too. Historical buyer records remain in the database and their dashboard read paths are unchanged.

## Unchanged protections and remaining gaps

Buyer authority, RFQ linkage, saved quote requirements, commercial approval and acceptance operations remain. Stronger missing-metadata blocking and the approval-consumption/acceptance transaction redesign were excluded from this scope. The final send hook's incomplete exact-approval enforcement also remains a separate gap. Supplier-ID-to-channel binding has not been redesigned here.

## Deployment and rollback

These are separate backend and wrapper Git repositories. Keep existing unrelated local migration work separate when committing. For this update, the backend must support `history_limit: 0` before the new plugin requests it. If deploying the existing uncommitted OpenClaw-analysis migration too, honor its wrapper/backend dependency order in README; coordinate both releases rather than deploying either entire dirty working tree blindly.

The edited plugin is built/packed by the Dockerfile and copied into the persistent extension by the entrypoint. Reloading the old container alone does not install these local source edits. No deployment has been performed.

Rollback only this change's hunks; do not reset either repository or discard the prior migration. Keep supplier-conflict records intact for manual resolution.

## Verification boundaries

Offline tests use mocked database and WhatsApp requests. They cover normalized and duplicate phone numbers (including later pages), tenant isolation, inactive/deleted supplier lookups, optional history recovery, short-reply activation, pause/greeting/no-context cases, per-product tool payloads, mapping-tool availability, denied delivery and analysis tool isolation. Existing route tests also exercise invitation ambiguity.

A controlled live follow-up still needs to verify actual session-history availability, product interpretation, conditional agreement, sent recipients and saved terms, along with before/after whole-turn token usage. No token reduction or live semantic accuracy is claimed from offline tests.
