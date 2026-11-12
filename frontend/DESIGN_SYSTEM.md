# Amafah procurement workspace

Design direction: minimal operational dashboard, informed by the requested UI UX Pro Max skill (https://github.com/nextlevelbuilder/ui-ux-pro-max-skill).

## Visual language
- Reference palette: blue actions (#1644d8), lime accents (#b6e00f), charcoal navigation/text (#191920), lavender surfaces (#eeeef7), cool gray borders and coral destructive accents (#ed7048).
- Bulk upload follows upload → shared values → item review. Category and deadline have independent apply actions, with a no-change category choice and an empty deadline leaving deadlines unchanged. Only selected rows are included in the submitted CSV. Compact rows keep product, category, quantity, deadline and prices visible; extra details expand individually or together. Search and 20-item pagination keep large uploads bounded.
- Multi-select pickers dismiss on outside click, keyboard Tab leaving the picker, or Escape. Recipient pickers show selected suppliers after closing.
- RFQ entry is grouped into item details, suppliers & timing, and price & negotiation, with in-page section shortcuts and explicit row/label gaps. Dialogs use white surfaces and readable dark text. Category chips wrap with an 8px gap. Native select arrows sit 14px inside the right edge with reserved text padding.
- Status success states use dark teal on a pale blue surface; lime is reserved for navigation accents.
- White text on solid blue and ranking badges; dark text on lime. Status colors retain explicit labels.
- Inter with system fallbacks, restrained borders and shadows, no decorative gradients.
- Shared semantic tokens live in src/index.css. Use these tokens for future screens.

## Layout and access
- Desktop sidebar groups workspace tasks and activity; create request stays prominent.
- At 900px and below, navigation becomes a visible top grid. No destination is hidden.
- Requests show key facts and recorded quotes first. Product specifications, negotiation authority, supplier progress and full supplier messages expand on demand.
- The three request actions share equal width and height.
- Wide tables scroll inside keyboard-focusable regions; no information columns are removed.
- Native buttons, current-page navigation labels, visible focus, skip link and reduced-motion support.

## Scope and verification
Existing business operations, backend APIs, approvals and procurement policies are preserved.
Mocked browser coverage checks navigation, dashboard actions, supplier modal, team settings, sign-out and screen widths from 375px to 1440px. Screenshots use fixture data, not live procurement records.
