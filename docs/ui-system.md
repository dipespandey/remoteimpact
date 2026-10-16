# Remote Impact typography and layout

All public pages, tools, account screens and staff workspaces use the light theme in `frontend/ui.css` and the type scale in `frontend/tailwind.config.js`. Build with `npm --prefix frontend run build` after changes.

- Body and form text: 16px; long job descriptions: 17px from the small-screen breakpoint upwards, 16px on phones.
- Secondary body text and controls: `text-sm` (15px). Metadata, help and badges: `text-xs` (14px). Avoid one-off 11–13px text. The dense timezone hour grid is an intentional exception.
- Page headings: `.page-title` (28–36px); section headings: `.section-title` (20px); card headings: `.card-title` (18px). Use the shared classes rather than shrinking headings to metadata size.
- Reading text: `.prose`; job descriptions also use `.reading-copy`. Use a readable measure (job content caps at 74ch), clear paragraph rhythm and no spacer paragraphs.
- Controls grow with wrapped text. Mobile buttons retain 44px minimum touch height. Do not force a fixed height onto multiline buttons.
- Job pages put the description before ancillary inventory comparisons. Source-status uncertainty stays visible; the detailed checking history expands on request.
- Use 16–24px within panels and 24–32px between related sections. Keep multi-column gaps proportional to their content. Do not add empty cards to balance a layout.

Check desktop, 390px mobile and an intermediate width when changing shared styles. Verify long titles, empty states, forms, tables and navigation. Staff fixture checks must not send outreach, mutate accounts or expose customer records. Keep table scrolling inside its panel.

The October 16 update normalizes 111 existing style/template files rather than adding a second visual theme. Two unreferenced legacy Hire for Mission templates contain pre-existing Django syntax/filter errors; they are not routed pages and were not activated by this styling change.
