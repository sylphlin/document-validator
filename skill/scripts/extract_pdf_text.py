#!/usr/bin/env python3
"""Extract a PDF into Markdown, page-by-page, for the document-validator skill.

Regulation and submission reports cite evidence as "[Doc-R1] p.4" or
"[Doc-S2] §3.2", so output keeps page boundaries intact using "## Page N"
headings. Markdown is used (rather than plain text or JSON) because it reads
naturally to an LLM doing semantic analysis, renders tables as real tables
instead of jumbled text, and keeps token overhead low compared to JSON.

Per page:
  - Tables are detected first and converted to Markdown table syntax.
  - Remaining text (with table regions excluded, to avoid duplicating table
    content into the prose) is extracted as normal paragraphs.
  - Images are not extracted, but their presence is noted so a reviewer knows
    to check the original PDF for figures, photos, or scanned attachments.
  - Pages with little or no extractable content (no real text, no tables) are
    flagged as likely scanned/image-based.
  - Pages dense with vector graphics (CAD drawings, 3D renderings) are
    classified via pdfium (see PDF_DRAWING_PAGE_VECTOR_THRESHOLD below) and
    routed to a separate fast path instead of pdfplumber/pdfminer — see
    "Drawing pages" below.

Drawing pages (CAD/architectural exports with huge vector counts) are handled
by pdfium (the pypdfium2 package — already an existing transitive dependency
of pdfplumber, used here directly) instead of pdfplumber. pdfplumber's table
detection is built on pdfminer, a pure-Python content-stream parser whose cost
scales with the *total* number of objects on a page — for a CAD export with
hundreds of thousands of vector primitives, merely reading page.lines/
.curves/.rects (the property access used to classify the page) can take over a
minute, before any actual extraction happens. pdfium is a compiled engine
(the same one Chrome uses to render PDFs) and classifies + extracts text from
the same pages in milliseconds to low seconds — see the threshold check below.
pdfium has no table-structure detection of its own, so non-drawing pages still
go through pdfplumber unchanged; only pages that fail the drawing-page check
skip it entirely.

Pages within a chunk are processed in parallel across worker processes (table
detection is the slow part of pdfplumber and is pure-Python/CPU-bound, so
threads wouldn't help here — each worker is a separate process with its own
GIL). Default worker count comes from $PDF_EXTRACT_WORKERS if set (it should
match the deployment's actual CPU quota — os.cpu_count() alone can't be
trusted to reflect that inside a container), else a conservative guess;
override with --workers for a specific call. Each worker opens its own copy
of the PDF, so peak memory scales with worker count — for very large PDFs,
more workers means more memory, not just more speed; if a chunk OOMs, reduce
--workers
before reducing chunk size.

A single page (typically one with a large or complex embedded image) can take
far longer than the rest of the chunk combined. $PDF_PAGE_TIMEOUT_SECONDS
(default 30) caps how long any one page gets before it's skipped and flagged
for manual review — without this, a single bad page would only be caught by
the whole script's SCRIPT_TIMEOUT_SECONDS, which kills the entire chunk
(losing every page that did finish) instead of just the one page that didn't.

For large PDFs, use --start/--end to pull the document in page-range chunks
instead of extracting the whole file at once.

Usage:
  python3 extract_pdf_text.py FILE.pdf
  python3 extract_pdf_text.py FILE.pdf --start 1 --end 20 --out chunk1.md
  python3 extract_pdf_text.py FILE.pdf --start 1 --end 20 --workers 4
  python3 extract_pdf_text.py FILE.pdf --summary-only
"""

import argparse
import concurrent.futures
import os
import signal
import sys
import time

import pdfplumber
import pypdfium2 as pdfium


def log_progress(message):
    """Per-page progress to stderr, timestamped, so a hang or crash mid-chunk still
    leaves a record of which page it got stuck on — stdout is reserved for the
    actual extracted Markdown, which the caller treats as the result."""
    print(f"[{time.strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)

# Pages with less than this many characters of text/table content are likely
# scanned images rather than real text, and should be flagged rather than
# silently treated as empty sections.
SCANNED_PAGE_CONTENT_THRESHOLD = 20

# A single page — typically one with a large or complex embedded image —
# can take far longer than the rest of the document combined, and the only
# safety net otherwise is the whole script's SCRIPT_TIMEOUT_SECONDS, which
# kills the entire chunk (losing every page that *did* finish) and keeps
# accumulating memory the whole time it's stuck. This caps a single page on
# its own, so one bad page is skipped and flagged instead of taking the
# whole chunk down with it. 0 disables this (no per-page cap).
PAGE_TIMEOUT_SECONDS = int(os.getenv("PDF_PAGE_TIMEOUT_SECONDS", "30"))


class _PageTimeoutError(Exception):
    pass


def _extract_page_markdown_with_timeout(page, page_num, pdfium_page=None):
    """Wraps extract_page_markdown with a per-page wall-clock cap.

    Uses SIGALRM rather than a thread/process, since this already runs as the
    sole work of a worker process (or the main process in sequential mode) —
    no extra process to manage, and Python delivers the signal as soon as
    control returns to the interpreter, which is enough to interrupt CPU-bound
    parsing of a single oversized page without affecting any other page.

    The alarm handler raises _PageTimeoutError, but pdfplumber's lazy layout
    parsing (triggered by accessing page.lines/.curves/.rects, or
    .find_tables()/.extract_text()) catches *any* exception mid-parse and
    re-wraps it as pdfplumber.utils.exceptions.PdfminerException — so the
    exception that actually reaches here is rarely _PageTimeoutError itself.
    A flag set by the handler (checked regardless of the final exception
    type/wrapping) is what actually identifies a timeout; only when that flag
    is unset does a caught exception get re-raised as a real error.

    This cap is now mostly a safety net rather than the primary defense for
    drawing pages — pdfium_page (when provided) lets extract_page_markdown
    classify and, if needed, fully extract a CAD-heavy page in milliseconds to
    low seconds, well before this would ever fire. It still matters for pages
    that are slow for some other reason pdfium's check doesn't catch.
    """
    if PAGE_TIMEOUT_SECONDS <= 0:
        return extract_page_markdown(page, pdfium_page)

    timed_out = {"value": False}

    def alarm_handler(signum, frame):
        timed_out["value"] = True
        raise _PageTimeoutError()

    previous_handler = signal.signal(signal.SIGALRM, alarm_handler)
    signal.alarm(PAGE_TIMEOUT_SECONDS)
    try:
        return extract_page_markdown(page, pdfium_page)
    except Exception:
        if not timed_out["value"]:
            raise
        log_progress(
            f"page {page_num}: timed out after {PAGE_TIMEOUT_SECONDS}s — likely a large or "
            "complex embedded image. Skipping and flagging for manual review."
        )
        return (
            "*[Page processing timed out — likely a large or complex embedded image. "
            "Manual review needed.]*",
            True,
            0,
        )
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)

# pdfplumber's default table-detection strategy looks for ruling lines, which
# matches most government forms and regulation tables. If a document uses
# whitespace-aligned tables with no visible lines, override with
# {"vertical_strategy": "text", "horizontal_strategy": "text"}.
TABLE_SETTINGS = {}

# find_tables()'s ruling-line detection groups every line/curve/rect on the
# page looking for grid structure, and its cost scales with how many of those
# objects exist — a CAD drawing or 3D architectural rendering can have
# thousands (sometimes hundreds of thousands) of short vector segments (fine
# detail, dimension marks, hatching) that aren't a table at all, and running
# table detection on them can take minutes for a page with nothing to find.
# A page this dense with vector graphics is treated as a drawing/diagram and
# routed to _is_drawing_page/pdfium (below) instead of pdfplumber entirely.
DRAWING_PAGE_VECTOR_THRESHOLD = int(os.getenv("PDF_DRAWING_PAGE_VECTOR_THRESHOLD", "800"))

# FPDF_PAGEOBJ_PATH — the pdfium page-object type for vector path segments
# (lines, curves, rects). Re-exposed as a module constant so tests can fake
# objects with a matching `.type` without importing pdfium's raw bindings.
PDFIUM_PATH_OBJECT_TYPE = pdfium.raw.FPDF_PAGEOBJ_PATH


def _is_drawing_page(pdfium_page, threshold):
    """Cheap drawing-page classification via pdfium instead of pdfplumber.

    pdfplumber's page.lines/.curves/.rects force pdfminer (pure Python) to
    fully tokenize the page's entire content stream just to answer "how many
    vector objects are there" — for a page with hundreds of thousands of
    them, that alone can take over a minute, before the threshold check even
    gets to make its decision. pdfium is a compiled engine and iterating its
    page objects is fast enough that, combined with an early exit the moment
    `threshold` is passed, classifying even a pathological page costs
    milliseconds: we don't need the exact count, just whether it's over the
    line, so there's no reason to keep counting past it.
    """
    count = 0
    for obj in pdfium_page.get_objects():
        if obj.type == PDFIUM_PATH_OBJECT_TYPE:
            count += 1
            if count > threshold:
                return True, count
    return False, count


# One pdfplumber.PDF handle per worker process, opened once via _init_worker
# rather than once per page — reused across every page that worker handles.
# A pdfium.PdfDocument is opened alongside it for the fast drawing-page path.
_worker_pdf = None
_worker_pdfium = None


def _init_worker(pdf_path):
    global _worker_pdf, _worker_pdfium
    _worker_pdf = pdfplumber.open(pdf_path)
    _worker_pdfium = pdfium.PdfDocument(pdf_path)


def rows_to_markdown(rows):
    if not rows:
        return ""

    def clean(cell):
        if cell is None:
            return ""
        return str(cell).replace("|", "\\|").replace("\n", " ").strip()

    ncols = max(len(row) for row in rows)

    def pad(row):
        cells = [clean(c) for c in row]
        return cells + [""] * (ncols - len(cells))

    lines = ["| " + " | ".join(pad(rows[0])) + " |"]
    lines.append("|" + "|".join(["---"] * ncols) + "|")
    for row in rows[1:]:
        lines.append("| " + " | ".join(pad(row)) + " |")
    return "\n".join(lines)


def extract_page_markdown(page, pdfium_page=None):
    # pdfium's check below is the primary gate — cheap enough to run on every
    # page. The pdfplumber-based check further down still runs for whatever
    # pdfium didn't flag: it's a no-op cost-wise on genuinely normal pages
    # (their vector count is low by definition), and a safety net for the
    # rare page where the two engines' object counts disagree near the
    # threshold, or where pdfium_page wasn't available at all (e.g. tests
    # exercising the pdfplumber-only path directly).
    if pdfium_page is not None:
        is_drawing, vector_object_count = _is_drawing_page(pdfium_page, DRAWING_PAGE_VECTOR_THRESHOLD)
        if is_drawing:
            # pdfium's text extraction is a separate code path from its path-object
            # iteration above — it only reads text-showing operators, never the
            # vector/path data, so paragraph text on an otherwise CAD-heavy page
            # (titles, notes, labels) is recovered instead of thrown away with it.
            text = (pdfium_page.get_textpage().get_text_range() or "").strip()
            parts = []
            if text:
                parts.append(text)
            parts.append(
                f"*[Page appears to be a technical drawing/diagram (>{DRAWING_PAGE_VECTOR_THRESHOLD} "
                "vector graphic objects detected) — drawing content not extracted, but any text on "
                "the page is included above. Visual review still needed to confirm what the drawing "
                "shows; do not assume it satisfies a requirement without checking.]*"
            )
            return "\n\n".join(parts), True, 0

    vector_object_count = len(page.lines) + len(page.curves) + len(page.rects)
    if vector_object_count > DRAWING_PAGE_VECTOR_THRESHOLD:
        text = (page.extract_text() or "").strip()
        parts = []
        if text:
            parts.append(text)
        parts.append(
            f"*[Page appears to be a technical drawing/diagram ({vector_object_count} vector "
            "graphic objects detected) — content not extracted. Visual review needed to confirm "
            "what the drawing shows; do not assume it satisfies a requirement without checking.]*"
        )
        return "\n\n".join(parts), True, 0

    tables = page.find_tables(table_settings=TABLE_SETTINGS)
    table_bboxes = [t.bbox for t in tables]

    def outside_all_tables(obj):
        x0, y0, x1, y1 = obj["x0"], obj["top"], obj["x1"], obj["bottom"]
        for tx0, ty0, tx1, ty1 in table_bboxes:
            if x0 >= tx0 - 1 and x1 <= tx1 + 1 and y0 >= ty0 - 1 and y1 <= ty1 + 1:
                return False
        return True

    text_page = page.filter(outside_all_tables) if table_bboxes else page
    text = (text_page.extract_text() or "").strip()

    md_tables = [rows_to_markdown(t.extract()) for t in tables]

    content_length = len(text) + sum(len(t) for t in md_tables)
    likely_scanned = content_length < SCANNED_PAGE_CONTENT_THRESHOLD

    parts = []
    if text:
        parts.append(text)
    for md_table in md_tables:
        if md_table:
            parts.append(md_table)
    if page.images:
        parts.append(f"*[{len(page.images)} image(s) detected on this page — not extracted, may need manual review]*")
    if likely_scanned:
        parts.append("*[No extractable text or tables — likely a scanned image. OCR or manual review needed.]*")

    return "\n\n".join(parts), likely_scanned, len(tables)


def _process_page(page_num):
    """Runs inside a worker process — operates on that worker's own PDF handle."""
    log_progress(f"pid {os.getpid()}: starting page {page_num}")
    started = time.time()
    page = _worker_pdf.pages[page_num - 1]
    pdfium_page = _worker_pdfium[page_num - 1]
    try:
        md, likely_scanned, n_tables = _extract_page_markdown_with_timeout(page, page_num, pdfium_page)
    finally:
        pdfium_page.close()
    log_progress(f"pid {os.getpid()}: finished page {page_num} ({time.time() - started:.1f}s)")
    return page_num, md, likely_scanned, n_tables


def _extract_with_log(pdf, pdfium_pdf, page_num):
    log_progress(f"starting page {page_num}")
    started = time.time()
    pdfium_page = pdfium_pdf[page_num - 1]
    try:
        result = _extract_page_markdown_with_timeout(pdf.pages[page_num - 1], page_num, pdfium_page)
    finally:
        pdfium_page.close()
    log_progress(f"finished page {page_num} ({time.time() - started:.1f}s)")
    return result


def process_pages_sequentially(pdf_path, page_numbers):
    log_progress(f"processing {len(page_numbers)} page(s) sequentially: {page_numbers[0]}-{page_numbers[-1]}")
    with pdfplumber.open(pdf_path) as pdf, pdfium.PdfDocument(pdf_path) as pdfium_pdf:
        return {
            p: _extract_with_log(pdf, pdfium_pdf, p)
            for p in page_numbers
        }


def process_pages_in_parallel(pdf_path, page_numbers, workers):
    """Returns {page_num: (markdown, likely_scanned, n_tables)}.

    Falls back to sequential processing if the worker pool itself dies (e.g. a
    worker was OOM-killed processing a page with a large embedded image) rather
    than failing the whole chunk — slower, but it still finishes. This is also
    why workers are separate processes rather than threads: a memory spike on
    one page kills only that worker, not the agent process serving the rest of
    the conversation.
    """
    if workers <= 1 or len(page_numbers) <= 1:
        return process_pages_sequentially(pdf_path, page_numbers)

    log_progress(
        f"processing {len(page_numbers)} page(s) with {workers} workers: "
        f"{page_numbers[0]}-{page_numbers[-1]}"
    )
    results = {}
    try:
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=workers, initializer=_init_worker, initargs=(pdf_path,)
        ) as executor:
            for page_num, md, likely_scanned, n_tables in executor.map(_process_page, page_numbers):
                results[page_num] = (md, likely_scanned, n_tables)
        return results
    except concurrent.futures.process.BrokenProcessPool:
        log_progress(
            f"[warning] worker pool crashed (likely out of memory with {workers} workers) — "
            "retrying this chunk sequentially with --workers 1. Consider lowering "
            "PDF_EXTRACT_WORKERS or raising AGENT_MEMORY for this document."
        )
        return process_pages_sequentially(pdf_path, page_numbers)


def default_worker_count():
    """Precedence: --workers flag > PDF_EXTRACT_WORKERS env var > a conservative guess.

    os.cpu_count() reads the host machine's CPU count, not the container's actual
    CPU quota (e.g. AGENT_CPU in a deployed agent) — those can differ a lot in a
    containerized environment, so it's only used as a last-resort fallback. Set
    PDF_EXTRACT_WORKERS explicitly (matching AGENT_CPU, or lower to leave headroom
    for the rest of the agent) rather than relying on this guess in production.
    """
    env_value = os.getenv("PDF_EXTRACT_WORKERS")
    if env_value:
        return max(1, int(env_value))
    return max(1, min(os.cpu_count() or 1, 4))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", help="Path to the PDF file")
    parser.add_argument("--start", type=int, default=1, help="First page to extract (1-indexed)")
    parser.add_argument("--end", type=int, default=None, help="Last page to extract (inclusive)")
    parser.add_argument("--out", default=None, help="Write extracted Markdown to this file instead of stdout")
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Worker processes for parallel page extraction (default: $PDF_EXTRACT_WORKERS, else min(CPU count, 4); use 1 to disable parallelism)",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Only print page count, table count, and which pages look scanned/image-based; skip full extraction",
    )
    args = parser.parse_args()
    workers = args.workers if args.workers else default_worker_count()

    with pdfplumber.open(args.pdf) as pdf:
        total_pages = len(pdf.pages)
    start = max(1, args.start)
    end = args.end if args.end and args.end <= total_pages else total_pages
    page_numbers = list(range(start if not args.summary_only else 1, (end if not args.summary_only else total_pages) + 1))

    results = process_pages_in_parallel(args.pdf, page_numbers, workers)

    if args.summary_only:
        scanned_pages = [p for p in page_numbers if results[p][1]]
        total_tables = sum(results[p][2] for p in page_numbers)

        print(f"Total pages: {total_pages}")
        print(f"Total tables detected: {total_tables}")
        if scanned_pages:
            print(
                f"Likely scanned/image-based pages ({len(scanned_pages)}): "
                + ", ".join(str(p) for p in scanned_pages)
            )
            print(
                "These pages returned little or no extractable content. Treat them as "
                "image-based per the skill's 'image-based or scanned' handling guideline."
            )
        else:
            print("No scanned/image-based pages detected — all pages contain extractable content.")
        return

    out = open(args.out, "w") if args.out else sys.stdout
    scanned_pages = []
    try:
        for page_num in page_numbers:
            md, likely_scanned, _ = results[page_num]
            if likely_scanned:
                scanned_pages.append(page_num)
            out.write(f"## Page {page_num}\n\n{md}\n\n")
    finally:
        if args.out:
            out.close()

    if args.out:
        print(f"Extracted pages {start}-{end} of {total_pages} to {args.out} (workers={workers})", file=sys.stderr)
        if scanned_pages:
            print(
                f"Flagged {len(scanned_pages)} likely scanned page(s): "
                + ", ".join(str(p) for p in scanned_pages),
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
