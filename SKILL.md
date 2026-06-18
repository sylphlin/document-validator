---
name: document-validator
description: >
  Use this skill when a user needs to validate whether a submission document meets
  a given set of regulations or requirements. Triggers when the user provides a
  regulation or requirement document AND a submission document to check against it.
  Also trigger when the user says "validate this document", "check compliance",
  "review this application", "does this meet the requirements", or anything involving
  checking a document against rules or standards. Use this skill whenever both a
  standard and a document to check are present in context, even if the request is
  phrased casually.
---

# Document Validator Skill

## Overview

Validates a submission document against a provided regulation or requirement document.
Produces a structured audit report with confidence-scored gap analysis — every
requirement gets a coverage score, every gap gets an explanation and a correction
suggestion. The output is an actionable, defensible audit trail, not just a verdict.

---

## Inputs Required

**Regulation set** — one or more documents that together define the standard to validate against.
Any format is accepted: article lists, checklists, policy text, tender specs, etc.

**Submission set** — one or more documents that together form the submission to be reviewed.
Typically includes a main application document plus attachments such as technical reports,
consent letters, or assessment results.

At least one document on each side is required. If either side is missing, ask the user
to provide it before proceeding.

---

## Workflow Overview

```
Phase 0: Intake
      ↓
Phase 1: Compliance Profile Extraction   ← parse regulation → build requirement list
      ↓  [checkpoint: user confirms]
Phase 2: Document Matching & Scoring     ← scan submission → score each requirement
                                            (includes logic & consistency check)
      ↓
Phase 3: Report Generation               ← single standard report
```

---

## Phase 0: Intake

### 0.1 Build the Document Inventory

Ask the user to identify all documents on each side and their role:

```
Please list all documents you are providing:

Regulation set:
  - Main regulation: {name}
  - Supporting documents (if any): {name, role}

Submission set:
  - Main submission: {name}
  - Attachments (if any): {name, role — e.g. "Appendix A: Traffic Impact Assessment"}
```

Confirm the inventory before proceeding:

```
Document inventory confirmed.

Regulation set:
  [R1] {name} — primary standard
  [R2] {name} — {role}

Submission set:
  [S1] {name} — main submission
  [S2] {name} — {role}
  [S3] {name} — {role}
```

Assign short document IDs (R1, R2 for regulation; S1, S2, S3 for submission)
for traceability throughout the report.

### 0.2 Check for Unstructured Documents

If any regulation document is unstructured (not a clean checklist or article list),
announce: "The regulation document is unstructured. I will parse it to extract all
reviewable requirements before starting the validation."

---

## Phase 1: Compliance Profile Extraction

**Goal:** Parse the regulation document and produce a structured requirement
checklist called the Compliance Profile.

### 1.1 Parse the Regulation Document

Regardless of format, extract and classify every requirement:

| Type | Definition | Notes |
|------|-----------|-------|
| **Disqualifying** | Explicitly stated in the regulation as a condition for rejection or non-acceptance | Any failure triggers Return filing immediately, regardless of other scores |
| **Mandatory** | Must be present or met unconditionally | Failure = deficiency |
| **Conditional** | Required only when a trigger condition applies | Score only if trigger applies |
| **Advisory** | Recommended but not required | Note but do not mark as deficient |

When parsing the regulation document, actively scan for explicit disqualifying language
such as "will not be accepted", "shall be rejected", "application is void if",
or equivalent phrasing in any language. Classify those requirements as Disqualifying.

If no disqualifying conditions are found in the regulation document, note this in the
Compliance Profile summary and apply the default disposition rules in Phase 3.

Also extract:
- **Format / length limits** — page count, required attachments, referenced forms
- **Required terminology** — specific terms the submission must use or reference

### 1.2 Build the Compliance Profile

Represent each requirement as a structured record:

```
REQ-{N}
Type:         Disqualifying / Mandatory / Conditional / Advisory
Source:       [R{N}] §{section or article reference}
Requirement:  {One-sentence description of what is required}
Check method: Field presence / Keyword match / Numeric or format check / Logic consistency
Trigger:      {For Conditional only — state the condition; leave blank otherwise}
```

### 1.3 Checkpoint — Confirm Compliance Profile

Present a summary before proceeding:

```
Compliance Profile ready.

Total requirements extracted: {N}
  Disqualifying: {N}  ← failure on any of these triggers immediate Return filing
  Mandatory:     {N}
  Conditional:   {N}
  Advisory:      {N}

Disqualifying conditions found in regulation: {Yes — list them / No — default rules apply}
Required terminology: {list}

Please confirm this looks complete. Add any missing requirements before I begin scoring.
```

Wait for user confirmation before moving to Phase 2.

---

## Phase 2: Document Matching & Scoring

Scan the submission document and score each requirement in the Compliance Profile.

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
- Do referenced attachments actually exist in the submission?
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
REQ-{N}: evidence found in [S1] §3.2 and [S3] p.7
```

When a requirement is met across multiple documents, list all sources.
When no evidence is found in any document, note "not found in any submission document."

### 2.5 Handling Ambiguous Cases

When a submission partially addresses a requirement, show the reasoning inline:

```
[Ambiguous match] REQ-{N}: {requirement description}

Source: [S{N}] {section or page reference}
Matched passage: "{quoted text from submission}"
Score: {X}% — {label}
Rationale: {what is covered and what is missing}
Interpretation applied: {if a reasonable interpretation was used, explain it}
Flag for manual review: {yes/no — explain if yes}
```

Flag for manual review when:
- The regulation language itself is vague (e.g., "attach relevant documents"
  without specifying which)
- The submission's intent is reasonable but wording deviates significantly
  from required terminology
- A judgment call is needed that exceeds textual analysis

---

## Phase 3: Report Generation

The report language follows the language of the input documents. All descriptive text,
notes, and suggestions are written in that language. The following identifiers are
system tracking symbols and are never translated: REQ-N, R1/R2/R3, S1/S2/S3.

```
# Submission Validation Report

Submission:   {document name(s)}
Regulation:   {regulation name(s)}
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

| ID    | Requirement   | Result | Score | Source     | Notes                      |
|-------|--------------|--------|-------|------------|----------------------------|
| REQ-1 | {description} | ✅     | 95%   | [S1] §3.1  | {brief note}               |
| REQ-2 | {description} | ⚠️    | 74%   | [S1] §4.2  | {what is missing or vague} |
| REQ-3 | {description} | 🚫    | 5%    | —          | {not found}                |

### Conditional Requirements

| ID    | Requirement   | Trigger applies? | Result  | Score | Source    | Notes  |
|-------|--------------|-----------------|---------|-------|-----------|--------|
| REQ-X | {description} | Yes              | ⚠️     | 78%   | [S2] §2.1 | {note} |
| REQ-Y | {description} | No               | ➖ N/A  | —     | —         | —      |

### Advisory Requirements

| ID    | Requirement   | Result           | Notes                              |
|-------|--------------|------------------|------------------------------------|
| REQ-Z | {description} | ⚠️ Not followed  | {note — for reference, not scored} |

---

## Gap Details

{Cover only items scored below 90%.}

When multiple requirements are deficient due to the same missing document or the same
root cause, consolidate them into a single Gap entry. List all affected REQ-N identifiers
together. This makes the report easier to act on — the submitting party sees one clear
action item instead of repeated entries for the same underlying gap.

**REQ-{N} [, REQ-{N}, ...]: {shared description if consolidated, or individual requirement}**
- What is missing or insufficient: {specific explanation}
- Evidence found in: {[S{N}] §{section}, or "not found in any submission document"}
- Regulation reference: {[R{N}] §{section or article} [, R{N}] §{...} if consolidated}
- Deficiency type: Correctable / Substantive / Indeterminate
- Suggested correction: {what the submitting party should add or fix, or "N/A — substantive non-compliance" / "Indeterminate — requires manual review"}

---

## Items Requiring Manual Review

{List all items flagged as "Requires manual review" during scoring.
If none, write: "No items require manual review."}

| ID    | Requirement   | Reason for manual review                  | Regulation reference |
|-------|--------------|-------------------------------------------|----------------------|
| REQ-{N} | {description} | {why automated scoring was not possible} | [R{N}] §{section}   |

```

### Disposition Rules

**Step 1 — Check for regulation-defined criteria**
If the regulation document defines explicit acceptance or rejection conditions,
apply those first. They take precedence over all rules below. Note in the report
which regulation article the disposition is based on.

**Step 2 — Apply default rules if no regulation-defined criteria exist**
Note in the report: "No explicit disposition criteria found in regulation. Default
rules applied."

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

**Regulation document is image-based or scanned**
Notify the user that the document appears to be image-based and that a text version
will give more reliable results. Proceed with best-effort extraction and flag any
requirements that could not be reliably read.

**Multiple documents provided on either side**
Treat all regulation documents as a unified standard — requirements may be spread
across the main document and supporting references. Treat all submission documents
as a unified submission — evidence for any requirement may appear in the main
document or in any attachment. Always record the specific source document ID and
location for every piece of evidence found.

**Submission document is very long**
Process section by section and announce progress. Do not skip sections.

**Requirement involves subjective judgment**
Do not assign a score. Flag the item as "Requires manual review" and describe
what the reviewer should look for when making the judgment call.

**Regulation language is vague**
State the interpretation applied and flag the item for reviewer confirmation
before finalizing the verdict on that item.

**Referenced attachment is listed but not provided**
Flag the item as "Requires manual review." Note that the attachment was referenced
in the submission but not available for review. Do not assume its contents satisfy
any requirement.

**Date-range compliance checks**
When a regulation specifies a maximum elapsed time between two dates (e.g. "document
must have been issued within X months of the application date"), always show the
calculation explicitly before stating the verdict. The required steps are:

1. State the start date (e.g. document issue date)
2. State the stated period (e.g. 3 months)
3. Compute and state the expiry date by adding the period to the start date using
   calendar months, not fixed day counts
4. State the reference date to compare against (e.g. application submission date)
5. Compare the reference date against the expiry date and state the verdict

Example:
- Issue date: 2024-01-15
- Validity period: 3 months
- Expiry date: 2024-04-15
- Application date: 2024-03-20
- 2024-03-20 is before 2024-04-15 → Compliant

Do not skip steps or compare day counts directly. The explicit date comparison is
required for every date-range check.
