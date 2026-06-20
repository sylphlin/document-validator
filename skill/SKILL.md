---
name: document-validator
description: >
  Use this skill when a user needs to validate whether a pending document meets
  a given set of standards or requirements. Triggers when the user provides a
  criteria document (regulation, tender spec, review-committee comments, etc.) AND a
  pending document to check against it. Also trigger when the user says "validate
  this document", "check compliance", "review this application", "does this meet the
  requirements", or anything involving checking a document against rules or criteria —
  not necessarily a government regulation. Use this skill whenever both criteria and a
  document to check are present in context, even if the request is phrased casually.
---

# Document Validator Skill

## Overview

Validates a pending document against provided criteria. The criteria don't
have to be a government regulation — they can be any document that defines what's
required: a tender specification, a review committee's comments, an internal
checklist, etc. Produces a structured audit report with confidence-scored gap
analysis — every requirement gets a coverage score, every gap gets an explanation
and a correction suggestion. The output is an actionable, defensible audit trail,
not just a verdict.

---

## Inputs Required

**Criteria Documents** — one or more documents that together define what the pending documents must
satisfy. Any format is accepted: regulations, article lists, checklists, policy text,
tender specifications, review-committee comments, etc.

**Pending Documents** — one or more documents that together form what's being checked against
the criteria. Typically includes a main document (an application, a bid, a project plan)
plus attachments such as technical reports, consent letters, or assessment results.

At least one document on each side is required. If either side is missing, ask the user
to provide it before proceeding.

Documents can be provided either as direct uploads or as Google Drive links (useful
when a file is too large to paste into chat) — see §0.1 for how Drive links are
fetched and processed.

## Response Language

Respond in the language the user is writing in — every reply in this skill,
conversational narration and the final report alike, not just the report. Do not
switch to the language of the input documents being analyzed; the documents are
the subject matter, not the language to respond in.

For Chinese specifically, Traditional and Simplified are different target outputs,
not interchangeable — match whichever script the user is actually typing in
(Traditional vs Simplified), and keep it consistent for the rest of the
conversation once established. If the user explicitly asks for a different
language or script than what they're typing in, honor that instead. If the
user's language/script cannot be determined from what they've written so far,
default to Traditional Chinese (zh-TW) rather than guessing toward whichever
variant is statistically more common in general.

System tracking symbols (REQ-{ID}, C-{N}, P-{N}) are never translated or
transliterated, regardless of response language — see Phase 3 for the full list.

**A note on running scripts:** every command shown in this document as
`python3 scripts/{name}.py ...` refers to one of exactly two scripts that exist in
this skill: `extract_pdf_text.py` and `fetch_drive_file.py`. There is no third
script for any other step — if a task in this document doesn't name one of these
two files, it's meant to be done by reasoning directly, not by calling a script.
Whatever tool actually executes these (a shell, or a `start_job`-style tool) takes
the bare filename — drop the `scripts/` prefix and the `python3` prefix shown in the
examples; they're there to make the command copyable, not to be passed literally.

---

## Calling Scripts: Launch, Poll, and Narrate

Every script call in this document goes through two tools instead of one blocking
call: **`start_job`** launches a script in the background and returns a `job_id`
immediately; **`check_job`** polls that `job_id` for its status. Neither tool
blocks waiting for the script to finish. This matters because the chat surface
this skill runs behind may apply its own timeout to a single conversational turn
that has nothing to do with how long a script legitimately needs — extracting a
140-page criteria document, or downloading a 111MB pending document from Drive, can easily
take longer than that turn-level timeout even though the script itself is working
correctly. Launching it in the background and polling decouples the two.

**The pattern for every script call:**
1. `start_job` with the script name and its arguments → get back a `job_id`.
2. `check_job` with that `job_id`. This call itself waits a few seconds before
   returning if the job hasn't finished yet — don't call it again immediately
   after getting `[status] running` back; that wait is already real elapsed
   time, not wasted time. For a job you expect to take a while, pass a larger
   `wait_seconds` (up to the max) instead of polling rapidly.
   - `[status] running` → see "Narrate progress, not every poll" below.
   - the script's actual output → the job is done, move on.
   - `[error] ...` → handle it per that script's own error guidance (e.g. §0.4's
     notes on timeout errors).
3. While one job is running, you can `start_job` additional independent work
   instead of waiting idle — e.g. the next chunk, or a second document's
   extraction. They run concurrently in the background.

**Narrate progress, not every poll.** A background job is invisible to the user
unless you say something — silence for a long stretch reads as "stuck." But
narrating *every single* `check_job` call produces a wall of repetitive "still
waiting" messages that's worse than silence — it buries the one update that
actually matters. Say something when you start a job, and again roughly once
every 30–60 seconds of real elapsed waiting (a handful of `check_job` calls,
not one), or whenever the situation actually changes (e.g. you now know why it's
slow, or you've decided to adjust your approach) — not on every individual
`[status] running` response. This applies just as much when the long-running
thing is your own reasoning rather than a script — e.g. scoring dozens of
requirements in Phase 2 has no job to poll, but narrate progress at natural
checkpoints anyway (e.g. "Scored requirements 1–10 of 42...") rather than after
every single one.

**Never mention internal tool or script names, file paths, or your own
troubleshooting process in what you say to the user.** "Still downloading the
file from Drive..." is fine; "checking fetch_drive_file.py's
parameters to find the best way to read this" or "trying /dev/stdout" is not —
the user doesn't need or want to know which script, which flag, or what you tried
that didn't work. Describe what's happening in plain, functional terms: what
document, what step, roughly how far along. Save implementation-level detail for
when something has genuinely failed and a human needs to intervene.

**End the turn only if something is genuinely still running**, not as a default
habit after every chunk. If everything you started finishes within the same turn,
keep going — start the next chunk, keep narrating, and only stop when the phase is
actually done or something is still in flight. When you do need to end a turn
early, say plainly what's still running and that you'll continue automatically.

---

## Workflow Overview

```
Phase 0: Intake
      ↓
Phase 1: Criteria Checklist Extraction   ← parse criteria → build requirement list
      ↓  [checkpoint: user confirms]
Phase 2: Document Matching & Scoring     ← scan pending documents → score each requirement
                                            (includes logic & consistency check)
      ↓
Phase 3: Report Generation               ← single standard report
```

---

## Phase 0: Intake

### 0.0 Back Up Directly-Uploaded Files

This skill may run in a deployed agent whose container instance can be swapped out
between turns — anything written only to local disk can disappear from one turn to
the next. A file fetched via a Google Drive link (§0.1) doesn't need backing up,
since it can always be re-fetched from Drive again. A file the user **pasted or
uploaded directly into the conversation** has no such fallback, so as soon as it's
saved to local disk, back it up once with:

```
python3 scripts/gcs_state.py upload-file --session-id {session-id} --user-id {user-id} --file /tmp/{doc-id}.pdf
```

`{session-id}` and `{user-id}` are given to you at the start of this conversation —
use them as-is. If they were not provided (e.g. running as a Claude Code skill
rather than a deployed Agent Engine agent), skip this step entirely; there is no
container-swap risk to guard against in that environment. If a file ever goes
missing locally later in the same conversation, restore it with `download-file`
instead of asking the user to re-upload.

### 0.1 Accepting Documents via Google Drive Link

Large files often cannot be pasted directly into chat, so a Google Drive link is an
expected input — e.g. "Here is the pending document: https://drive.google.com/file/d/abc123/view".
When a message contains a `drive.google.com` or `docs.google.com` URL, treat it as a
document the same way an uploaded file would be treated, fetched via
`scripts/fetch_drive_file.py`.

This script calls the Google Drive API directly (not a chat-client connector or MCP
tool), since this skill may run in environments — such as an ADK agent deployed on
Google Agent Engine — where no such connector exists. It authenticates with
Application Default Credentials (ADC): whatever service account or user credential
is configured in the runtime environment. **The target file must be shared with that
identity** — for a service account, that means sharing the file with the service
account's email address specifically, not just "anyone with the link."

**1. Check what the link points to** (via `start_job`/`check_job` — see "Calling
Scripts" above):

```
python3 scripts/fetch_drive_file.py "{drive-url}"
```

With no `--out`, this just prints the file's name, MIME type, and size — enough to
confirm the link resolves and decide how to handle it next, and to see the file
size before committing to a download. If this fails (file not found, no access),
tell the user plainly: "I can't access this Drive link — please confirm the file
exists and is shared with the service account this agent runs as, then send the
link again." Do not guess at the document's content from the URL or filename alone.

**2. Fetch the content, normalizing everything to the same pipeline used for uploaded files.**
A large file (e.g. a 111MB document) can take a while to download — start the job,
then poll and narrate per the "Calling Scripts" pattern (e.g. "Still downloading
{name} (111MB) from Drive..." on each check that comes back running):

| Target | Command | Then |
|--------|---------|------|
| PDF file | `python3 scripts/fetch_drive_file.py "{url}" --out /tmp/{doc-id}.pdf` | Run the saved file through §0.4 (PDF extraction) exactly as an uploaded PDF |
| Google-native doc (Docs/Sheets/Slides) | Same command — the script auto-exports these to PDF | Run through §0.4 — exporting to PDF first keeps page-citation conventions consistent across every document regardless of original source |
| Plain-text/Markdown file (not a PDF, not Google-native) | `python3 scripts/fetch_drive_file.py "{url}" --print-content` | Reads the content directly into context — no separate save/extract step needed |
| Folder link | `python3 scripts/fetch_drive_file.py "{url}" --list-only` | Lists every file inside with its own ID; repeat steps 1–2 for each one as its own inventory entry (criteria documents or pending documents, per what the user said the folder contains) |

For any other format that can't be exported to PDF and isn't plain text either,
download it with `--out` and note in the inventory that page-level citation may
not be available for that document; cite by section/heading instead.

**4. Record provenance in the document inventory** — note that the document came from
a Drive link rather than a direct upload, so the source is traceable if anyone needs
to re-verify against the original later (e.g. `[P-2] financial_statement.pdf — fetched from Google Drive`).

### 0.2 Build the Document Inventory

Ask the user to identify all documents on each side and their role:

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

Assign short document IDs (C-1, C-2 for the criteria documents; P-1, P-2, P-3
for the pending documents) for traceability throughout the report.

### 0.3 Check for Unstructured Documents

If any criteria document is unstructured (not a clean checklist or article list),
announce: "The criteria document is unstructured. I will parse it to extract all
reviewable requirements before starting the validation."

### 0.4 Extracting Text from PDF Documents

For PDF documents, use `scripts/extract_pdf_text.py` rather than relying on ad-hoc
reading. It converts each page to Markdown rather than plain text or JSON — Markdown
keeps page boundaries intact (needed for citations like "[P-1] p.4"), renders
tables as real Markdown tables instead of jumbled text, and stays token-efficient
compared to a JSON structure. Pages with little or no extractable content are
flagged as likely scanned/image-based, and detected images are noted (their content
is not extracted) so a reviewer knows to check the original PDF for figures or photos.

First, run a quick scan to see the document's size, table count, and whether any
pages need OCR or manual review:

```
python3 scripts/extract_pdf_text.py {file}.pdf --summary-only
```

Pages within a chunk are extracted in parallel across worker processes by default
(table detection is the slow, CPU-bound part of this script). The worker count comes
from the deployment's `PDF_EXTRACT_WORKERS` setting (see `.env.example`); pass
`--workers N` to override it for a specific call, or `--workers 1` to force
sequential processing if a chunk runs out of memory — each worker holds its own copy
of the PDF in memory, so more workers means more memory used, not just more speed.

Use `start_job`/`check_job` for this call (see "Calling Scripts" above) and narrate
each check (e.g. "Scanning {file}.pdf (42 pages)...", then "Still scanning..." on
any check that comes back running).

Then extract the content in chunks using `--start`/`--end`:

```
python3 scripts/extract_pdf_text.py {file}.pdf --start 1 --end 20 --out /tmp/{doc-id}-p1-20.md
python3 scripts/extract_pdf_text.py {file}.pdf --start 21 --end 40 --out /tmp/{doc-id}-p21-40.md
```

Keep chunks to roughly **20 pages**, not 50 — this isn't just about staying readable,
it's also about the script's execution timeout (`SCRIPT_TIMEOUT_SECONDS`, configured
per deployment — see `.env.example`): a chunk that runs long enough to approach that
limit fails outright with no partial output, which is worse than a slow response. A
smaller chunk finishes well under the timeout. If a single chunk still times out
(dense tables, very large pages), halve the range and retry rather than silently
giving up on those pages.

If a timeout error comes back, look for a "partial stdout/stderr before timeout"
section in it — the script logs which page each worker started and finished, with
timing, so the last line logged before the cutoff usually points straight at the
page that was slow or stuck. Don't just retry blindly; mention the specific page if
the same one keeps timing out, since that's a sign the page itself needs manual
review rather than a smaller chunk.

A single page — usually one with a large or complex embedded image — can also
get individually capped: if a page takes longer than `PDF_PAGE_TIMEOUT_SECONDS`
(default 30s, see `.env.example`), the script skips just that page and marks it
"*[Page processing timed out...]*" instead of letting it stall the whole chunk.
Dense technical drawings (CAD, 3D renderings) are detected even faster and
flagged as "*[Page appears to be a technical drawing/diagram...]*" without
spending the full timeout on them at all. Treat pages flagged either way the
same as scanned/image-based pages — they need manual review. **Trust the flag
immediately; don't re-extract or single-page-probe the same page hoping for a
different result** — the result won't change, and probing page by page to find
where drawings end and text resumes wastes calls that a table of contents (if
the document has one) already answers directly. If an early chunk's content
included a table of contents, use the section/page numbers it gives you to jump
straight to the next text-heavy section instead of guessing — e.g. if the TOC
shows a "Drawings" chapter spanning pages 7–25 followed by a "Regulatory Review"
chapter starting at page 26, extract pages 26+ next, not a string of single-page
probes at 20, 25, 30, 35... to find that boundary by trial and error.

Launch each chunk via `start_job` and narrate per the "Calling Scripts" pattern.
**Don't start the next chunk of the same file until the current one finishes** —
each chunk already uses up to `PDF_EXTRACT_WORKERS` worker processes internally,
so running two chunks of the same large PDF at once multiplies memory use and
risks the same out-of-memory failure the worker pool's fallback exists to recover
from. The "start something else while a job runs" concurrency from "Calling
Scripts" is for genuinely independent work instead — e.g. extracting a different
document, or fetching the next file from Drive — not more chunks of the same file.
Stay in the same turn and keep going chunk after chunk as long as each one keeps
finishing; only end the turn if a chunk is still running after you've given the
user a few status updates on it, stating which chunk it is and that you'll
continue automatically.

If `--summary-only` reports scanned/image-based pages, follow the "Criteria document
is image-based or scanned" guidance in Execution Guidelines below for those pages —
do not silently treat them as blank. If a page's table is not detected (e.g. a table
with no ruling lines), note this and fall back to manual transcription from the
extracted text for that page.

---

## Phase 1: Criteria Checklist Extraction

**Goal:** Parse the criteria document(s) and produce a structured requirement
list called the Criteria Checklist.

**If the criteria document(s) were extracted in multiple page-range chunks (§0.4), build the
Criteria Checklist incrementally, one chunk at a time** — narrate progress as you
go (e.g. "Extracted 14 requirements from pages 1-20. Continuing with pages
21-40.") the same way as §0.4. Reasoning through every requirement in a large
criteria document can take a while even with nothing technically blocking you, so keep
narrating at each chunk boundary so the user can see it's moving — stay in the same
turn across chunks as long as you're making steady progress, and only end the turn
early if you genuinely need more time than is reasonable for one turn. Only present
the full §1.3 checkpoint once every chunk has been processed and the running list
is complete.

**When chunking, checkpoint the running Criteria Checklist to GCS after each chunk**
— it's derived from your own reasoning, not raw document content, so there's no
cheap way to reconstruct it if the conversation is interrupted long enough for the
session to expire or land on a different container later. After updating the
running list, save it (the full structured list built so far, plus which page
ranges are done and which remain):

```
echo '{"requirements": [...], "completed_ranges": ["1-20"], "remaining_ranges": ["21-40"]}' | python3 scripts/gcs_state.py write-state --session-id {session-id} --user-id {user-id} --name criteria_checklist
```

At the very start of Phase 1, before parsing anything, check whether a checkpoint
already exists for this document:

```
python3 scripts/gcs_state.py read-state --session-id {session-id} --user-id {user-id} --name criteria_checklist
```

If one is found, resume from the `remaining_ranges` it lists instead of re-parsing
from page 1. If none is found (the normal case for a fresh validation), proceed as
described above. Skip both of these calls entirely if `{session-id}`/`{user-id}`
were not provided — same as §0.0.

### 1.1 Parse the Criteria Documents

Regardless of format, extract and classify every requirement:

| Type | Definition | Notes |
|------|-----------|-------|
| **Disqualifying** | Explicitly stated in the criteria as a condition for rejection or non-acceptance | Any failure triggers Return filing immediately, regardless of other scores |
| **Mandatory** | Must be present or met unconditionally | Failure = deficiency |
| **Conditional** | Required only when a trigger condition applies | Score only if trigger applies |
| **Advisory** | Recommended but not required | Note but do not mark as deficient |

When parsing the criteria document, actively scan for explicit disqualifying language
such as "will not be accepted", "shall be rejected", "application is void if",
or equivalent phrasing in any language. Classify those requirements as Disqualifying.

If no disqualifying conditions are found in the criteria, note this in the
Criteria Checklist summary and apply the default disposition rules in Phase 3.

Also extract:
- **Format / length limits** — page count, required attachments, referenced forms
- **Required terminology** — specific terms the pending documents must use or reference

When the criteria have a multi-level structure (e.g. chapter → article → paragraph,
or chapter → article → clause/item, regardless of the labeling convention used in
the original language), the requirement ID itself should mirror that structure
using dot notation, rather than a flat sequential counter:

```
Top-level item (e.g. Chapter 1, or Article 1 in a flat set of criteria) → REQ-1
Sub-item nested under it (e.g. Article 2 of Chapter 1)             → REQ-1.2
Sub-sub-item nested under that (e.g. Item 3 of that Article)       → REQ-1.2.3
```

This makes the ID itself traceable to its place in the criteria — anyone reading
"REQ-1.2.3" immediately knows it's the 3rd item under the 2nd article of chapter 1,
without needing to separately look up a section reference. Use this numbering
consistently everywhere a requirement is identified (Criteria Checklist, Detailed
Results, Gap Details, Manual Review queue). If the criteria have only one level of
structure (a flat list of articles with no sub-items), plain sequential IDs
(`REQ-1`, `REQ-2`, `REQ-3`...) are sufficient — don't invent nesting that isn't there.

### 1.2 Build the Criteria Checklist

Represent the requirements as a Markdown table, one row per requirement:

| ID | Type | Source | Requirement | Check method | Trigger |
|----|------|--------|-------------|---------------|---------|
| REQ-1.2.3 | Mandatory | [C-1] Article 2, Item 3 | {one-sentence description of what is required} | Field presence | — |
| REQ-3.1 | Conditional | [C-1] Article 4 | {one-sentence description of what is required} | Logic consistency | {the condition that triggers this requirement} |

- **ID** — the hierarchical REQ-{ID} from §1.1.
- **Type** — Disqualifying / Mandatory / Conditional / Advisory.
- **Source** — [C-{N}] plus the original document label (e.g. "Article 2, Item 3" or a page reference).
- **Check method** — Field presence / Keyword match / Numeric or format check / Logic consistency.
- **Trigger** — for Conditional rows only, state the condition; leave as `—` for every other type.

For a long table, build it the same incrementally-across-chunks way as the rest
of Phase 1 — append rows as each chunk is parsed rather than holding the whole
table until the end.

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

**If the user responds with a change** (add/remove/edit a requirement) instead of a
plain confirmation: apply the change, then briefly describe what was changed (e.g.
"Added REQ-7 per your note; removed REQ-3.2 as not applicable"), followed by the
**entire updated summary** in the same format above, and wait for confirmation
again. Do not proceed to Phase 2 on the same turn a change was applied; the user
needs to see the resulting checklist as a whole (a one-line change can shift totals,
interact with another requirement, or be misapplied) before approving it. Repeat
this apply → describe → re-present → wait loop for as many rounds as the user keeps
requesting changes. Only move on once a turn's response is an actual confirmation
with no further changes requested.

---

## Phase 2: Document Matching & Scoring

Scan the pending documents and score each requirement in the Criteria Checklist.

For a large Criteria Checklist, narrate progress in batches as you score (e.g.
"Scored requirements 1-10 of 42...") rather than going silent until everything is
scored — see "Calling Scripts" above; this applies even though scoring is your own
reasoning with no job to poll. Stay in the same turn across batches as long as
you're making steady progress.

### 2.1 Coverage Score Scale

| Score   | Label          | Meaning |
|---------|----------------|---------|
| 90–100% | ✅ Compliant   | Clearly addressed; content is complete |
| 70–89%  | ⚠️ Partial     | Present but incomplete or vague |
| 40–69%  | ❌ Weak        | Only indirectly related or severely insufficient |
| 0–39%   | 🚫 Missing     | No corresponding content found |

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
- Run this check on every validation and surface any contradictions in Gap Details.

### 2.3 Handling Conditional Requirements

Before scoring a Conditional requirement, verify whether the trigger condition applies.
If not, mark the requirement as N/A and exclude it from scoring.

### 2.4 Source Tracking

For every requirement scored, record which document(s) the evidence was found in.
Use the document IDs assigned in Phase 0.

When scoring, note the source inline:

```
REQ-{ID}: evidence found in [P-1] §3.2 and [P-3] p.7
```

When a requirement is met across multiple documents, list all sources.
When no evidence is found in any document, note "not found in any pending document."

### 2.5 Handling Ambiguous Cases

When the pending documents partially address a requirement, show the reasoning inline:

```
[Ambiguous match] REQ-{ID}: {requirement description}

Source: [P-{N}] {section or page reference}
Matched passage: "{quoted text from the pending documents}"
Score: {X}% — {label}
Rationale: {what is covered and what is missing}
Interpretation applied: {if a reasonable interpretation was used, explain it}
Flag for manual review: {yes/no — explain if yes}
```

Flag for manual review when:
- The criteria's language itself is vague (e.g., "attach relevant documents"
  without specifying which)
- The pending document's intent is reasonable but wording deviates significantly
  from required terminology
- A judgment call is needed that exceeds textual analysis

---

## Phase 3: Report Generation

The report follows the same response-language rule as the rest of this skill (see
"Response Language" above) — it does not switch to the input documents' language
just because that's what's being analyzed. The following identifiers are system
tracking symbols and are never translated regardless of language: REQ-{ID} (e.g.
REQ-1.2), C-1/C-2/C-3, P-1/P-2/P-3.

**Produce the report section by section, narrating as you go** (see "Calling
Scripts" above — this applies to your own generation, not just script jobs). For a
pending documents with dozens of requirements, generating the entire report — every
table, every Gap entry, every manual-review row — in one uninterrupted block can
itself take a while even though no script is involved. Say what's coming next
after each section below; stay in the same turn across sections as long as you're
producing steady output, and only end a turn early if generation is genuinely
taking long enough that a status update is overdue.

A reasonable split:
1. Executive Summary (short — compliance rate and disposition)
2. Detailed Results — Mandatory Requirements
3. Detailed Results — Conditional and Advisory Requirements
4. Gap Details
5. Items Requiring Manual Review

Say what's coming next at the end of each part (e.g. "Executive summary above —
detailed results for the 32 mandatory requirements next."), so the user knows more is
on the way rather than mistaking a section for the whole report.

```
# Document Validation Report

Pending document(s):   {document name(s)}
Criteria:                 {criteria document name(s)}
Case number:  {if provided by user, otherwise leave blank}
Review date:  {date}

---

## Executive Summary

Overall compliance rate: {X}%
- Compliant  ✅: {N} items
- Partial    ⚠️: {N} items
- Weak       ❌: {N} items
- Missing    🚫: {N} items
- N/A        ➖: {N} items (conditional requirements that do not apply)

Disposition recommendation: {see disposition rules below}

---

## Detailed Results

### Mandatory Requirements

| ID      | Requirement   | Result | Score | Source     | Notes                      |
|---------|--------------|--------|-------|------------|----------------------------|
| REQ-1.1 | {description} | ✅     | 95%   | [P-1] §3.1  | {brief note}               |
| REQ-1.2 | {description} | ⚠️    | 74%   | [P-1] §4.2  | {what is missing or vague} |
| REQ-2   | {description} | 🚫    | 5%    | —          | {not found}                |

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

{Cover only items scored below 90%.}

When multiple requirements are deficient due to the same missing document or the same
root cause, consolidate them into a single Gap entry. List all affected REQ-{ID}
identifiers together. This makes the report easier to act on — the party that
submitted the pending documents sees one clear action item instead of repeated entries for
the same underlying gap.

**REQ-{ID} [, REQ-{ID}, ...]: {shared description if consolidated, or individual requirement}**
- What is missing or insufficient: {specific explanation}
- Evidence found in: {[P-{N}] §{section}, or "not found in any pending document"}
- Criteria reference: {[C-{N}] §{original document label, e.g. "Article 2, Item 3"} [, C-{N}] §{...} if consolidated}
- Deficiency type: Correctable / Substantive / Indeterminate
- Suggested correction: {what should be added or fixed, or "N/A — substantive non-compliance" / "Indeterminate — requires manual review"}

---

## Items Requiring Manual Review

{List all items flagged as "Requires manual review" during scoring.
If none, write: "No items require manual review."}

| ID       | Requirement   | Reason for manual review                  | Criteria reference |
|----------|--------------|-------------------------------------------|----------------------|
| REQ-{ID} | {description} | {why automated scoring was not possible} | [C-{N}] §{original document label}   |

```

### Disposition Rules

**Step 1 — Check for explicit disposition conditions in the criteria**
If the criteria documents define explicit acceptance or rejection conditions,
apply those first. They take precedence over all rules below. Note in the report
which article or clause of the criteria the disposition is based on.

**Step 2 — Apply default rules if no explicit disposition conditions exist**
Note in the report: "No explicit disposition criteria found in the criteria documents.
Default rules applied."

- **Approve** — all Disqualifying and Mandatory requirements are Compliant (≥ 90%),
  and all applicable Conditional requirements are Compliant or Partial (≥ 70%),
  and no cross-document contradictions are found.

- **Request correction** — no Disqualifying requirements have failed, and one or
  more Mandatory or applicable Conditional requirements are Partial (70–89%) or
  Weak (40–69%), and all deficient items are classified as Correctable, and no
  cross-document contradictions involving Mandatory requirements are found.

- **Return filing** — any of the following apply:
  - Any Disqualifying requirement is not fully met
  - Any Mandatory or applicable Conditional requirement is Missing (< 40%)
  - Any deficient item is classified as Substantive (non-compliance cannot be
    resolved by supplementation)
  - A cross-document contradiction is found involving a Mandatory requirement

- **Escalate for review** — one or more items are classified as Indeterminate or
  flagged "Requires manual review", and the disposition cannot be determined without
  human judgment. Do not issue a final disposition. List the blocking items clearly.

---

## Execution Guidelines

These are standard situations to anticipate and handle during every validation.
They are not exceptions — treat them as part of normal execution.

**Criteria document is image-based or scanned**
Notify the user that the document appears to be image-based and that a text version
will give more reliable results. Proceed with best-effort extraction and flag any
requirements that could not be reliably read. `scripts/extract_pdf_text.py --summary-only`
identifies which specific pages fall into this category before extraction begins.

**A page could not be read (scanned, technical drawing, or timed out)**
`scripts/extract_pdf_text.py` flags pages it could not meaningfully process — scanned
images, dense CAD drawings/3D renderings, or a page that hit `PDF_PAGE_TIMEOUT_SECONDS`
— with an explicit note in the extracted Markdown instead of silently producing nothing.
If a requirement's evidence would be expected on one of these pages, flag that
requirement as "Requires manual review" and say which page and why (e.g. "p.9 — page is
a technical drawing, content not extracted"). **Never mark a requirement as satisfied
just because an unreadable page exists where a diagram or attachment was expected** —
its presence is not evidence of its content, the same as a referenced attachment that
was never provided (see below). Retrying extraction will not fix a page like this; it
needs an actual human to look at the rendered page.

**Multiple documents provided on either side**
Treat all criteria documents as a unified set of criteria — requirements may be spread
across the main document and supporting references. Treat all pending documents as a
single unified set — evidence for any requirement may appear in the main document or
in any attachment. Always record the specific source document ID and location for
every piece of evidence found.

**Pending document is very long (large PDF)**
Use `scripts/extract_pdf_text.py` with `--start`/`--end` to pull the document in
~20-page chunks rather than extracting the whole file at once (see §0.4 for why —
chunk size is tied to the script's execution timeout, not just readability). Launch
and narrate each chunk per §0.4's pattern, one at a time. Do not skip pages — a
missed page is a missed requirement or a missed piece of evidence.

**Requirement involves subjective judgment**
Do not assign a score. Flag the item as "Requires manual review" and describe
what the reviewer should look for when making the judgment call.

**Criteria's language is vague**
State the interpretation applied and flag the item for reviewer confirmation
before finalizing the verdict on that item.

**Referenced attachment is listed but not provided**
Flag the item as "Requires manual review." Note that the attachment was referenced
in the pending documents but not available for review. Do not assume its contents satisfy
any requirement.

**Date-range compliance checks**
When the criteria specify a maximum elapsed time between two dates (e.g. "document
must have been issued within X months of the application date"), always show the
calculation explicitly before stating the verdict. The required steps are:

1. State the start date (e.g. document issue date)
2. State the stated period (e.g. 3 months)
3. Compute and state the expiry date by adding the period to the start date using
   calendar months, not fixed day counts
4. State the reference date to compare against (e.g. the date the pending documents were submitted)
5. Compare the reference date against the expiry date and state the verdict

Example:
- Issue date: 2024-01-15
- Validity period: 3 months
- Expiry date: 2024-04-15
- Application date: 2024-03-20
- 2024-03-20 is before 2024-04-15 → Compliant

Do not skip steps or compare day counts directly. The explicit date comparison is
required for every date-range check.
