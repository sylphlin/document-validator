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
import sys

import pdfplumber

# Pages with less than this many characters of text/table content are likely
# scanned images rather than real text, and should be flagged rather than
# silently treated as empty sections.
SCANNED_PAGE_CONTENT_THRESHOLD = 20

# pdfplumber's default table-detection strategy looks for ruling lines, which
# matches most government forms and regulation tables. If a document uses
# whitespace-aligned tables with no visible lines, override with
# {"vertical_strategy": "text", "horizontal_strategy": "text"}.
TABLE_SETTINGS = {}

# One pdfplumber.PDF handle per worker process, opened once via _init_worker
# rather than once per page — reused across every page that worker handles.
_worker_pdf = None


def _init_worker(pdf_path):
    global _worker_pdf
    _worker_pdf = pdfplumber.open(pdf_path)


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


def extract_page_markdown(page):
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
    page = _worker_pdf.pages[page_num - 1]
    md, likely_scanned, n_tables = extract_page_markdown(page)
    return page_num, md, likely_scanned, n_tables


def process_pages_in_parallel(pdf_path, page_numbers, workers):
    """Returns {page_num: (markdown, likely_scanned, n_tables)}, processed across worker processes."""
    if workers <= 1 or len(page_numbers) <= 1:
        with pdfplumber.open(pdf_path) as pdf:
            return {
                p: extract_page_markdown(pdf.pages[p - 1])
                for p in page_numbers
            }

    results = {}
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=workers, initializer=_init_worker, initargs=(pdf_path,)
    ) as executor:
        for page_num, md, likely_scanned, n_tables in executor.map(_process_page, page_numbers):
            results[page_num] = (md, likely_scanned, n_tables)
    return results


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
