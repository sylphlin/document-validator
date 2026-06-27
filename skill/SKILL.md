---
name: document-validator
description: >
  Use this skill when a user needs to validate whether a pending document meets
  given criteria. Triggers when the user provides a criteria document (regulation,
  tender spec, review-committee comments, etc.) AND a pending document to check
  against it. Also trigger on "validate this document", "check compliance",
  "review this application", "does this meet the requirements", or anything
  involving checking a document against rules or criteria — not necessarily a
  government regulation. Use whenever both criteria and a document to check are
  present in context, even if phrased casually.
---

# Document Validator Skill

## Overview

Validates a pending document against provided criteria — which can be any
document that defines what's required (regulation, tender spec, review-committee
comments, internal checklist), not just a government regulation. Produces a
structured audit report with confidence-scored gap analysis: every requirement
gets a coverage score, every gap gets an explanation and a correction suggestion.
The output is an actionable, defensible audit trail, not just a verdict.

---

## Inputs Required

**Criteria Documents** — one or more documents that together define what the
pending documents must satisfy. Any format: regulations, article lists,
checklists, policy text, tender specs, review-committee comments, etc.

**Pending Documents** — one or more documents being checked against the
criteria. Typically a main document (application, bid, project plan) plus
attachments (technical reports, consent letters, assessment results).

At least one document on each side is required; ask the user for whichever is
missing before proceeding. Documents can be direct uploads or Google Drive links
(see §0.1) — useful when a file is too large to paste into chat.

## Response Language

Respond in the language the user is writing in — narration and the final report
alike. Do not switch to the language of the documents being analyzed; they're the
subject matter, not the response language.

For Chinese specifically, Traditional and Simplified are different target
outputs, not interchangeable — match whichever script the user is typing in and
keep it consistent once established. Honor an explicit request for a different
language/script. If undetermined, default to Traditional Chinese (zh-TW) rather
than guessing toward whichever variant is more common in general.

System tracking symbols (REQ-{ID}, C-{N}, P-{N}) are never translated or
transliterated, regardless of response language.

---

## Running Scripts

This skill bundles exactly two scripts: `extract_pdf_text.py` and
`fetch_drive_file.py`. If a step below doesn't name one of these, do it by
reasoning directly — there is no script for it. Commands are shown as
`python3 scripts/{name}.py ...`; whatever tool actually runs them takes the bare
filename, not the `scripts/` or `python3` prefix shown.

Every script call goes through two tools instead of one blocking call:
**`start_job`** launches it in the background and returns a `job_id`;
**`check_job`** polls that `job_id`. This decouples a script's real duration from
the chat surface's own per-turn timeout, which is unrelated to — and often
shorter than — how long a script may legitimately need.

**Pattern:**
1. `start_job(script, args)` → `job_id`.
2. `check_job(job_id, wait_seconds)` — already waits internally (default 5s, up
   to 15s) before returning, so don't re-call it the instant you see
   `[status] running`. Pass a larger `wait_seconds` for jobs expected to take a
   while, instead of polling rapidly.
3. While one job runs, `start_job` other genuinely independent work (a different
   document, the next file from Drive) instead of waiting idle.

**Narrate, but not every poll.** Say something when a job starts, then again
roughly every 30–60 seconds of real waiting — not on every single
`[status] running`. Same for your own reasoning when there's no job to poll (e.g.
scoring in Phase 2): narrate at natural checkpoints, not after every item.

**Never expose script/tool names or your own troubleshooting to the user.**
Describe progress in plain terms — what document, what step, how far along — not
which file or flag you used.

**Only end a turn if something is genuinely still running.** Keep going chunk
after chunk in the same turn as long as each one finishes; when you do need to
stop early, say plainly what's still in flight.

---

## Workflow Overview

```
Phase 0: Intake
      ↓
Phase 1: Criteria Checklist Extraction   ← parse criteria → build requirement list
      ↓  [checkpoint: user confirms]
Phase 2: Document Matching & Scoring     ← scan pending documents → score each requirement
      ↓
Phase 3: Report Generation               ← single standard report
```

---

## Phase 0: Intake

### 0.0 Back Up Directly-Uploaded Files

This skill may run in a deployed agent whose container instance can be swapped
between turns — anything on local disk only can disappear. A file fetched via a
Drive link (§0.1) doesn't need backing up, since it can be re-fetched. A file the
user **pasted or uploaded directly** has no such fallback — back it up once it's
on local disk:

```
python3 scripts/gcs_state.py upload-file --session-id {session-id} --user-id {user-id} --file /tmp/{doc-id}.pdf
```

`{session-id}`/`{user-id}` are given to you at conversation start; use them
as-is, or skip this step entirely if not provided (e.g. running as a Claude Code
skill, where there's no container-swap risk). If a file goes missing later in the
conversation, restore it with `download-file` instead of asking the user to
re-upload.

### 0.1 Accepting Documents via Google Drive Link

A Drive link is an expected input for large files (e.g. "Here is the pending
document: https://drive.google.com/file/d/abc123/view"). Treat any
`drive.google.com`/`docs.google.com` URL as a document.

**If the `fetch_drive_file_oauth` tool is available, use it instead of the
steps below.** It authenticates as the signed-in user — files only need to be
shared with that user normally, not with a deployed service account. Pass it
the URL or bare ID directly (no `start_job`/`check_job` needed — it returns
synchronously). If it returns `{"status": "pending_auth", ...}`, tell the user
to complete the Google sign-in prompt, then call it again with the same
`url_or_id`; don't treat this as an error or retry with the script below.

Otherwise, fall back to `scripts/fetch_drive_file.py`, which calls the Drive
API directly (not a chat-client connector) — this is what's available when no
OAuth tool or connector exists. It authenticates via Application Default
Credentials — **the target file must be shared with that identity** (for a
service account, its email address specifically, not "anyone with the link").

**1. Check what the link points to:**

```
python3 scripts/fetch_drive_file.py "{drive-url}"
```

With no `--out`, this just prints name/MIME type/size — enough to confirm access
and size before downloading. On failure, tell the user plainly: "I can't access
this Drive link — please confirm it's shared with the service account this agent
runs as." Do not guess content from the URL or filename.

**2. Fetch the content**, normalizing to the same pipeline used for uploads.
Start the job, then poll and narrate per "Running Scripts" above:

| Target | Command | Then |
|--------|---------|------|
| PDF file | `python3 scripts/fetch_drive_file.py "{url}" --out /tmp/{doc-id}.pdf` | Run through §0.4 like an uploaded PDF |
| Google-native doc (Docs/Sheets/Slides) | Same command — auto-exports to PDF | Run through §0.4 — exporting to PDF first keeps page-citation conventions consistent regardless of source |
| Plain-text/Markdown file | `python3 scripts/fetch_drive_file.py "{url}" --print-content` | Reads content directly into context — no save/extract step needed |
| Folder link | `python3 scripts/fetch_drive_file.py "{url}" --list-only` | Lists every file with its own ID; repeat steps 1–2 for each as its own inventory entry |

**3. Other formats** (can't export to PDF, isn't plain text): download with
`--out` and note in the inventory that page-level citation may not be available;
cite by section/heading instead.

**4. Record provenance** in the document inventory — note the document came from
a Drive link, so it's traceable later (e.g. `[P-2] financial_statement.pdf —
fetched from Google Drive`).

### 0.2 Build the Document Inventory

Ask the user to identify all documents and their role:

```
Please list all documents you are providing:

Criteria Documents:
  - Main criteria document: {name}
  - Supporting documents (if any): {name, role}

Pending Documents:
  - Main pending document: {name}
  - Attachments (if any): {name, role — e.g. "Appendix A: Traffic Impact Assessment"}
```

Confirm the inventory before proceeding:

```
Document inventory confirmed.

Criteria Documents:
  [C-1] {name} — primary criteria document
  [C-2] {name} — {role}

Pending Documents:
  [P-1] {name} — main pending document
  [P-2] {name} — {role}
  [P-3] {name} — {role}
```

These short IDs (C-1, C-2... / P-1, P-2, P-3...) carry traceability through the
rest of the report.

### 0.3 Check for Unstructured Documents

If a criteria document is unstructured (not a clean checklist or article list),
announce: "The criteria document is unstructured. I will parse it to extract all
reviewable requirements before starting the validation."

### 0.4 Extracting Text from PDF Documents

Use `scripts/extract_pdf_text.py` rather than ad-hoc reading. It converts each
page to Markdown — preserving page numbers for citation (e.g. "[P-1] p.4"),
rendering tables as real Markdown tables, and flagging pages with little or no
extractable content as likely scanned. Images are noted, not extracted.

First, scan the document:

```
python3 scripts/extract_pdf_text.py {file}.pdf --summary-only
```

This reports page count, table count, and which pages need OCR or manual
review — narrate it via `start_job`/`check_job`.

Then extract in chunks of roughly **20 pages**, not 50:

```
python3 scripts/extract_pdf_text.py {file}.pdf --start 1 --end 20 --out /tmp/{doc-id}-p1-20.md
```

20 pages keeps a chunk safely under the script's execution timeout
(`SCRIPT_TIMEOUT_SECONDS`) — a chunk that runs long enough to approach it fails
outright with no partial output. If a chunk still times out, halve the range and
retry. The timeout error includes a partial stdout/stderr log of which page each
worker reached — if the same page keeps stalling, flag it for manual review
instead of retrying again.

A single slow page (large/complex image) is capped individually by
`PDF_PAGE_TIMEOUT_SECONDS` (default 30s) and marked
`*[Page processing timed out...]*` rather than stalling the whole chunk. Pages
dense with vector graphics (CAD/3D drawings) are detected even faster and marked
`*[Page appears to be a technical drawing...]*`. **Trust either flag
immediately — don't re-extract or single-page-probe to verify**, the result
won't change. If an earlier chunk's table of contents shows where a drawings
section ends and text resumes, jump straight there instead of probing page by
page to find the boundary.

Pages are extracted in parallel by default (`PDF_EXTRACT_WORKERS`, see
`.env.example`); each worker holds its own copy of the PDF, so more workers also
means more memory — use `--workers 1` if a chunk runs out of memory. **Don't run
two chunks of the same file concurrently** — each already uses up to
`PDF_EXTRACT_WORKERS` processes, so two at once doubles the memory risk. (The
"start independent work while a job runs" rule above means a *different*
document, not more chunks of this one.)

Stay in the same turn across chunks as long as each one finishes; only end early
if a chunk is genuinely still running after a few status updates.

If `--summary-only` flags scanned/image-based pages, follow "Criteria document is
image-based or scanned" in Execution Guidelines below.

**Large PDFs — kick off in the background.** When a criteria PDF is large
enough that extracting it inline would be slow, call `start_async_validation`
with the criteria document reference(s) and `response_language` describing the
language you are currently responding in (per "Response Language" above) — the
background job runs with no conversation context, so this is the only way it
knows what language to write the checklist in. It returns a job_id immediately
and runs extraction + Criteria Checklist building in the background. Do NOT
poll it and do NOT call check_job on it — instead, tell the user the document
is being processed and that the checklist will appear on their next message,
then end your turn. When the job finishes, the completed Criteria Checklist is
surfaced to the user automatically; you do not need to rebuild or re-announce
it.

---

## Phase 1: Criteria Checklist Extraction

**Goal:** Parse the criteria document(s) into a structured requirement list, the
Criteria Checklist.

If the criteria were extracted in multiple chunks (§0.4), build the checklist
incrementally — narrate progress at each chunk boundary, stay in the same turn as
long as you're making progress, and present the full §1.3 checkpoint only once
every chunk is processed.

**Checkpoint to GCS after each chunk** — the checklist is derived from your own
reasoning, not raw document content, so it can't be cheaply reconstructed if the
session is interrupted long enough to expire or land on a different container:

```
echo '{"requirements": [...], "completed_ranges": ["1-20"], "remaining_ranges": ["21-40"]}' | python3 scripts/gcs_state.py write-state --session-id {session-id} --user-id {user-id} --name criteria_checklist
```

At the start of Phase 1, check for an existing checkpoint first:

```
python3 scripts/gcs_state.py read-state --session-id {session-id} --user-id {user-id} --name criteria_checklist
```

If found, resume from `remaining_ranges` instead of re-parsing from page 1. Skip
both calls if `{session-id}`/`{user-id}` were not provided (same as §0.0).

### 1.1 Parse the Criteria Documents

Extract and classify every requirement, regardless of format:

| Type | Definition | Notes |
|------|-----------|-------|
| **Disqualifying** | Explicitly stated in the criteria as a condition for rejection or non-acceptance | Any failure triggers Return filing immediately, regardless of other scores |
| **Mandatory** | Must be present or met unconditionally | Failure = deficiency |
| **Conditional** | Required only when a trigger condition applies | Score only if trigger applies |
| **Advisory** | Recommended but not required | Note but do not mark as deficient |

Actively scan for explicit disqualifying language ("will not be accepted",
"shall be rejected", "application is void if", or equivalent in any language) and
classify those as Disqualifying. If none are found, note this in the checklist
summary and apply the default disposition rules in Phase 3.

Also extract:
- **Format / length limits** — page count, required attachments, referenced forms
- **Required terminology** — specific terms the pending documents must use or reference

When the criteria have a multi-level structure (chapter → article → paragraph,
or similar regardless of original labeling), mirror that structure in the
requirement ID via dot notation rather than a flat counter:

```
Top-level item (e.g. Chapter 1, or Article 1 in a flat set of criteria) → REQ-1
Sub-item nested under it (e.g. Article 2 of Chapter 1)                  → REQ-1.2
Sub-sub-item nested under that (e.g. Item 3 of that Article)            → REQ-1.2.3
```

This makes the ID itself traceable to its place in the criteria — "REQ-1.2.3" is
immediately the 3rd item under the 2nd article of chapter 1, no separate lookup
needed. Use this numbering consistently everywhere a requirement appears
(Criteria Checklist, Detailed Results, Gap Details, Manual Review queue). If the
criteria have only one level of structure, plain sequential IDs (`REQ-1`,
`REQ-2`...) are sufficient — don't invent nesting that isn't there.

### 1.2 Build the Criteria Checklist

Represent the requirements as a Markdown table, one row per requirement:

| ID | Type | Source | Requirement | Check method | Trigger |
|----|------|--------|-------------|---------------|---------|
| REQ-1.2.3 | Mandatory | [C-1] Article 2, Item 3 | {one-sentence description} | Field presence | — |
| REQ-3.1 | Conditional | [C-1] Article 4 | {one-sentence description} | Logic consistency | {triggering condition} |

- **ID** — the hierarchical REQ-{ID} from §1.1.
- **Type** — Disqualifying / Mandatory / Conditional / Advisory.
- **Source** — [C-{N}] plus the original label (e.g. "Article 2, Item 3" or a page reference).
- **Check method** — Field presence / Keyword match / Numeric or format check / Logic consistency.
- **Trigger** — for Conditional rows only; `—` for every other type.

**Optional "Proposed by" column** — if the criteria has a clear individual or
unit attached to each requirement (e.g. a review committee's comments, where
each comment is attributed to a specific member or department), add a
"Proposed by" column after Source:

| ID | Type | Source | Proposed by | Requirement | Check method | Trigger |
|----|------|--------|-------------|-------------|---------------|---------|
| REQ-2.1 | Mandatory | [C-1] Comment 3 | Committee Member: Dr. Chen | {one-sentence description} | Field presence | — |

Only add this column when the criteria actually attribute requirements this
way — leave it out entirely for criteria with no such attribution (e.g. most
regulations and tender specs), rather than filling it with `—` on every row.

For a long table, build it incrementally across chunks like the rest of Phase
1 — append rows as each chunk is parsed.

### 1.3 Checkpoint — Confirm Criteria Checklist

Present a summary before proceeding:

```
Criteria Checklist ready.

Total requirements extracted: {N}
  Disqualifying: {N}  ← failure on any of these triggers immediate Return filing
  Mandatory:     {N}
  Conditional:   {N}
  Advisory:      {N}

Disqualifying conditions found in criteria: {Yes — list them / No — default rules apply}
Required terminology: {list}

Please confirm this looks complete. Add any missing requirements before I begin scoring.
```

Wait for user confirmation before moving to Phase 2.

**If the user responds with a change** (add/remove/edit a requirement) instead of
a plain confirmation: apply it, briefly describe what changed (e.g. "Added REQ-7
per your note; removed REQ-3.2 as not applicable"), then re-present the **entire
updated summary** in the same format and wait for confirmation again. Don't
proceed to Phase 2 on the same turn a change was applied — the user needs to see
the resulting checklist as a whole, since a one-line change can shift totals or
interact with another requirement. Repeat apply → describe → re-present → wait
for as many rounds as needed. Only move on once a turn's response is an actual
confirmation with no further changes.

---

## Phase 2: Document Matching & Scoring

Scan the pending documents and score each requirement in the Criteria Checklist.
For a large checklist, narrate progress in batches (e.g. "Scored requirements
1-10 of 42...") rather than going silent until everything is scored, even though
scoring is your own reasoning with no job to poll.

### 2.1 Coverage Score Scale

| Score   | Label          | Meaning |
|---------|----------------|---------|
| 90–100% | ✅ Compliant   | Clearly addressed; content is complete |
| 70–89%  | ⚠️ Partial     | Present but incomplete or vague |
| 40–69%  | ❌ Weak        | Only indirectly related or severely insufficient |
| 0–39%   | 🚫 Missing     | No corresponding content found |
| —       | 🔍 Indeterminate | The only available evidence is content that was never actually read (an image, scanned page, technical drawing, or a page that timed out) |

**Never assign Compliant/Partial/Weak/Missing based on content nobody actually
read.** A page being image-based, scanned, or flagged by
`extract_pdf_text.py` is not evidence of what it contains — it's the absence
of evidence, same as a referenced attachment that was never provided. Use
Indeterminate instead, route it to the Manual Review queue (§5 of the
report), and say what the reviewer needs to check (e.g. "p.9 — diagram, not
extracted; confirm it shows the required site layout"). This applies
regardless of how plausible the surrounding context makes compliance look —
a confident-sounding score without an actual read is worse than an honest
"can't tell."

### 2.2 Scoring Logic by Check Method

**Field Presence**
- 100% — section exists with substantive content
- 60% — section heading exists but content is sparse or placeholder-only
- 0% — section entirely absent

**Keyword / Terminology Match**
- Score based on proportion of required terms found
- Accept exact matches and clearly equivalent synonyms
- Flag non-standard wording even if the meaning is acceptable

**Numeric / Format Compliance**
- Binary: within limit = 100%; exceeds limit = 0%
- Record the exact value found alongside the required limit

**Logic & Consistency**
- Do facts in one section contradict facts in another?
- Do referenced attachments actually exist in the pending documents?
- Are figures, dates, and named parties consistent throughout?
- Run this check on every validation; surface contradictions in Gap Details.

### 2.3 Handling Conditional Requirements

Before scoring a Conditional requirement, verify whether the trigger condition
applies. If not, mark it N/A and exclude it from scoring.

### 2.4 Source Tracking

For every requirement scored, record which document(s) the evidence was found in,
using the IDs assigned in Phase 0:

```
REQ-{ID}: evidence found in [P-1] §3.2 and [P-3] p.7
```

List all sources when a requirement is met across multiple documents. When no
evidence is found anywhere, note "not found in any pending document."

### 2.5 Handling Ambiguous Cases

When the pending documents partially address a requirement, show the reasoning
inline:

```
[Ambiguous match] REQ-{ID}: {requirement description}

Source: [P-{N}] {section or page reference}
Matched passage: "{quoted text from the pending documents}"
Score: {X}% — {label, or "— Indeterminate" if flagged below}
Rationale: {what is covered and what is missing}
Interpretation applied: {if a reasonable interpretation was used, explain it}
Flag for manual review: {yes/no — explain if yes}
```

If "Flag for manual review" is yes, the Score must be Indeterminate, not a
numeric label — the two can never disagree. A confident-looking score paired
with a manual-review flag tells the reviewer nothing they can act on.

Flag for manual review when:
- The criteria's language itself is vague (e.g. "attach relevant documents" without specifying which)
- The pending document's intent is reasonable but wording deviates significantly from required terminology
- A judgment call is needed that exceeds textual analysis
- The only evidence available is content that wasn't actually read (image, scanned page, technical drawing) — see §2.1

---

## Phase 3: Report Generation

The report follows the same response-language rule as the rest of this skill
(see "Response Language" above) — it does not switch to the documents' language
just because that's what's being analyzed. These identifiers are system tracking
symbols, never translated: REQ-{ID} (e.g. REQ-1.2), C-1/C-2/C-3, P-1/P-2/P-3.

**Produce the report section by section, narrating as you go** — even with no
script involved, generating a long report in one uninterrupted block can itself
take a while. Say what's coming next after each part (e.g. "Executive summary
above — detailed results for the 32 mandatory requirements next."), and stay in
the same turn across sections as long as you're producing steady output.

A reasonable split:
1. Executive Summary (short — compliance rate and disposition)
2. Detailed Results — Mandatory Requirements
3. Detailed Results — Conditional and Advisory Requirements
4. Gap Details
5. Items Requiring Manual Review

```
# Document Validation Report

Pending document(s): {document name(s)}
Criteria:            {criteria document name(s)}
Case number:         {if provided by user, otherwise leave blank}
Review date:         {date}

---

## Executive Summary

Overall compliance rate: {X}%
- Compliant     ✅: {N} items
- Partial       ⚠️: {N} items
- Weak          ❌: {N} items
- Missing       🚫: {N} items
- Indeterminate 🔍: {N} items (could not be scored — see Manual Review)
- N/A           ➖: {N} items (conditional requirements that do not apply)

Disposition recommendation: {see disposition rules below}

---

## Detailed Results

### Mandatory Requirements

| ID      | Requirement   | Result | Score | Source     | Notes                      |
|---------|--------------|--------|-------|------------|----------------------------|
| REQ-1.1 | {description} | ✅     | 95%   | [P-1] §3.1  | {brief note}               |
| REQ-1.2 | {description} | ⚠️    | 74%   | [P-1] §4.2  | {what is missing or vague} |
| REQ-2   | {description} | 🚫    | 5%    | —          | {not found}                |
| REQ-2.3 | {description} | 🔍    | —     | [P-1] p.9   | {e.g. "diagram, not extracted — see Manual Review"} |

### Conditional Requirements

| ID      | Requirement   | Trigger applies? | Result  | Score | Source    | Notes  |
|---------|--------------|-----------------|---------|-------|-----------|--------|
| REQ-3.1 | {description} | Yes              | ⚠️     | 78%   | [P-2] §2.1 | {note} |
| REQ-3.2 | {description} | No               | ➖ N/A  | —     | —         | —      |

### Advisory Requirements

| ID    | Requirement   | Result           | Notes                              |
|-------|--------------|------------------|------------------------------------|
| REQ-4 | {description} | ⚠️ Not followed  | {note — for reference, not scored} |

---

## Gap Details

{Cover every item scored below 90%, plus every Indeterminate item — the
latter has no numeric score but is not Compliant either.}

When multiple requirements are deficient due to the same missing document or the
same root cause, consolidate into a single Gap entry listing all affected
REQ-{ID}s together — one clear action item instead of repeated entries for the
same underlying gap.

**REQ-{ID} [, REQ-{ID}, ...]: {shared description if consolidated, or individual requirement}**
- What is missing or insufficient: {specific explanation}
- Evidence found in: {[P-{N}] §{section}, or "not found in any pending document"}
- Criteria reference: {[C-{N}] §{original label, e.g. "Article 2, Item 3"} [, C-{N}] §{...} if consolidated}
- Deficiency type: Correctable / Substantive / Indeterminate
- Suggested correction: {what should be added or fixed, or "N/A — substantive non-compliance" / "Indeterminate — requires manual review"}

---

## Items Requiring Manual Review

{List all items flagged "Requires manual review" during scoring.
If none: "No items require manual review."}

| ID       | Requirement   | Reason for manual review                  | Criteria reference |
|----------|--------------|-------------------------------------------|----------------------|
| REQ-{ID} | {description} | {why automated scoring was not possible} | [C-{N}] §{original label}   |

```

### Disposition Rules

**Step 1 — Check for explicit disposition conditions in the criteria.** If the
criteria documents define explicit acceptance/rejection conditions, apply those
first; they take precedence over the rules below. Note which article/clause the
disposition is based on.

**Step 2 — Apply default rules if no explicit conditions exist.** Note: "No
explicit disposition criteria found in the criteria documents. Default rules
applied."

- **Approve** — all Disqualifying and Mandatory requirements are Compliant
  (≥ 90%), all applicable Conditional requirements are Compliant or Partial
  (≥ 70%), and no cross-document contradictions are found.

- **Request correction** — no Disqualifying requirement failed, one or more
  Mandatory/applicable Conditional requirements are Partial (70–89%) or Weak
  (40–69%), every deficient item is Correctable, and no cross-document
  contradiction involves a Mandatory requirement.

- **Return filing** — any of: a Disqualifying requirement is not fully met; a
  Mandatory/applicable Conditional requirement is Missing (< 40%); a deficient
  item is Substantive (cannot be resolved by supplementation); a cross-document
  contradiction involves a Mandatory requirement.

- **Escalate for review** — one or more items are Indeterminate or flagged
  "Requires manual review" and the disposition can't be determined without human
  judgment. Don't issue a final disposition; list the blocking items clearly.

---

## Execution Guidelines

These are routine situations to anticipate, not exceptions — handle them as part
of normal execution. Each is a self-contained item; add, remove, or edit one
without needing to touch the others.

- **Criteria document is image-based or scanned** — Notify the user, proceed
  with best-effort extraction, and score any requirement that could not be
  reliably read as Indeterminate (§2.1), not Compliant/Partial/Weak/Missing.
  `extract_pdf_text.py --summary-only` identifies affected pages up front.

- **A page could not be read (scanned, technical drawing, or timed out)** —
  `extract_pdf_text.py` marks pages it couldn't process with an explicit note
  in the Markdown rather than producing silent gaps. If a requirement's
  evidence would be expected on such a page, score it Indeterminate (§2.1) and
  flag it "Requires manual review" with the page and reason (e.g. "p.9 —
  technical drawing, content not extracted"). **Never mark a requirement
  Compliant just because an unreadable page exists where evidence was
  expected** — its presence isn't evidence of its content, same as a
  referenced attachment never provided. Retrying extraction won't fix this;
  it needs a human to look at the rendered page.

- **Multiple documents on either side** — Treat all criteria documents as one
  unified set of criteria, and all pending documents as one unified set —
  evidence or requirements may be spread across the main document and any
  attachment. Always record the specific source document ID and location for
  every piece of evidence.

- **Pending document is very long (large PDF)** — Use `--start`/`--end` to
  pull it in ~20-page chunks (§0.4) rather than all at once. Don't skip
  pages — a missed page is a missed requirement or piece of evidence.

- **Requirement involves subjective judgment** — Don't assign a score. Flag
  "Requires manual review" and describe what the reviewer should look for.

- **Criteria's language is vague** — State the interpretation applied and flag
  the item for reviewer confirmation before finalizing the verdict.

- **Referenced attachment is listed but not provided** — Flag "Requires manual
  review." Note that it was referenced but not available for review — don't
  assume its contents satisfy any requirement.

- **Date-range compliance checks** — When the criteria specify a maximum
  elapsed time between two dates (e.g. "must be issued within X months of the
  application date"), show the calculation explicitly:
  1. State the start date (e.g. document issue date)
  2. State the stated period (e.g. 3 months)
  3. Compute the expiry date using calendar months, not fixed day counts
  4. State the reference date to compare against (e.g. submission date)
  5. Compare reference date to expiry date and state the verdict

  Example: Issue date 2024-01-15, validity 3 months → expiry 2024-04-15.
  Application date 2024-03-20 is before 2024-04-15 → Compliant.

  Do not skip steps or compare day counts directly — every date-range check
  needs the explicit comparison.
