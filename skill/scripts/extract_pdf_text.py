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

For large PDFs, use --start/--end to pull the document in page-range chunks
instead of extracting the whole file at once.

Usage:
  python3 extract_pdf_text.py FILE.pdf
  python3 extract_pdf_text.py FILE.pdf --start 1 --end 50 --out chunk1.md
  python3 extract_pdf_text.py FILE.pdf --summary-only
"""

import argparse
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", help="Path to the PDF file")
    parser.add_argument("--start", type=int, default=1, help="First page to extract (1-indexed)")
    parser.add_argument("--end", type=int, default=None, help="Last page to extract (inclusive)")
    parser.add_argument("--out", default=None, help="Write extracted Markdown to this file instead of stdout")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Only print page count, table count, and which pages look scanned/image-based; skip full extraction",
    )
    args = parser.parse_args()

    with pdfplumber.open(args.pdf) as pdf:
        total_pages = len(pdf.pages)
        start = max(1, args.start)
        end = args.end if args.end and args.end <= total_pages else total_pages

        if args.summary_only:
            scanned_pages = []
            total_tables = 0
            for page_num in range(1, total_pages + 1):
                _, likely_scanned, n_tables = extract_page_markdown(pdf.pages[page_num - 1])
                total_tables += n_tables
                if likely_scanned:
                    scanned_pages.append(page_num)

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
            for page_num in range(start, end + 1):
                md, likely_scanned, _ = extract_page_markdown(pdf.pages[page_num - 1])
                if likely_scanned:
                    scanned_pages.append(page_num)
                out.write(f"## Page {page_num}\n\n{md}\n\n")
        finally:
            if args.out:
                out.close()

        if args.out:
            print(f"Extracted pages {start}-{end} of {total_pages} to {args.out}", file=sys.stderr)
            if scanned_pages:
                print(
                    f"Flagged {len(scanned_pages)} likely scanned page(s): "
                    + ", ".join(str(p) for p in scanned_pages),
                    file=sys.stderr,
                )


if __name__ == "__main__":
    main()
