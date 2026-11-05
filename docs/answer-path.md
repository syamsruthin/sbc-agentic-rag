# Answer path

How a compose draft becomes the answer users see (PLAN.md section 3.7), in `packages/agent/src/sbc_agent/answer`.

1. **Placeholders** (`placeholders.py`). The model writes values only as placeholders for what the tools
   returned this turn:
   - `{{F3}}` is a fact's SBC wording; `{{F3.label}}` and `{{F3.tier}}` its label and column heading;
   - `{{S2}}` quotes a text chunk;
   - `{{P1.name}}`, `.code`, `.year`, `.period` and `.type` name a plan.
2. **Verify** (`verify.py`), on the draft before anything is expanded. Each of these rejects the draft:
   - any digit, `$` or `%`, or a word that states an amount ("hundred", "percent", "free");
   - a placeholder for something no tool returned this turn, or an unknown field;
   - a link;
   - advice phrasing, for the public bot.
   A rejected draft goes back to compose once with the violations as feedback, then to the template fallback.
3. **Render** (`render.py`). Code adds everything that must be right:
   - a header line per plan: name, contract code, plan year, coverage period;
   - each value in the SBC's wording, followed by a numbered citation to the SBC section and page;
   - a "Note from the SBC" with the row's limitations for each benefit value, unless the answer quotes them;
     tiers that share a note are named together;
   - the carried-note label, *"In the SBC this note appears in a box continuing from the previous page."*;
   - citations of rows whose event heading isn't printed in the SBC name only the row;
   - the disclaimer and the not-affiliated line.
4. **Template fallback** (`fallback.py`). It lists every fact and excerpt of the turn as a placeholder-only
   draft, and it goes through the same verifier and renderer.

## SBC links for preview-only SBCs

About 1,383 SBCs (1,071 Small Group, 310 Individual) are served only through the Anthem portal's preview form, so
the bundle's `source_url` for them is the portal search page, not the PDF. Since bundle `2027.2` the bundle's
`plan_source` table holds each plan's preview-form fields and PDF sha256. The API opens the bundle with
`pdf_proxy_base="/api/sbc/"`, which points those plans' `source_url` at `GET /api/sbc/{plan_key}.pdf`, so their
citations link to the PDF at the cited page (`…pdf#page=N`). That endpoint fetches the PDF through the preview
form, checks its sha256 against the bundle, caches it for a day and streams it (`sbc_api.routes.sbc_pdf`). The
`sbc-ask` CLI, and bundles older than `2027.2`, keep the portal search page link.
