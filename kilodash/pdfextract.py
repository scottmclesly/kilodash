"""PDF text extraction worker for the Tables converter — run as a
SUBPROCESS, never in the web app's process (crash isolation: vendor PDFs
are hostile input, and a parser segfault/oom must kill this worker, not
the service). Invoked argv-style, no shell:

    python3 -m kilodash.pdfextract /path/to/upload.pdf

stdout: one JSON object {"pages": [str, …], "skeleton": {Canboat-subset}}
exit 0 on success, non-zero + stderr message on any failure.

The skeleton is deliberately assistive-only: PGN numbers spotted in the
text, one placeholder field each. Extraction will be wrong sometimes —
the converter's side-by-side review (human approves every table, never
auto/batch) is the safety mechanism, so this worker optimizes for recall
of *candidates*, not for being right.
"""

import json
import re
import sys

MAX_PAGES = 200
MAX_PAGE_CHARS = 20_000
MAX_CANDIDATES = 60

# "PGN 127508", "PGN: 130306" … and bare 5/6-digit numbers in the ranges
# NMEA2000 actually assigns (59392–61184 ISO, 65280–65535 proprietary-B,
# 126208–130842 marine).
_PGN_LABELLED = re.compile(r"PGN\s*:?\s*#?\s*(\d{4,6})", re.IGNORECASE)
_PGN_BARE = re.compile(r"\b(\d{5,6})\b")


def _plausible(n):
    return 59392 <= n <= 61184 or 65280 <= n <= 65535 \
        or 126208 <= n <= 130842


def candidates(pages):
    """Ordered, deduped plausible PGN numbers; labelled mentions first."""
    seen, out = set(), []

    def add(n):
        if _plausible(n) and n not in seen:
            seen.add(n)
            out.append(n)
    for text in pages:
        for m in _PGN_LABELLED.finditer(text):
            add(int(m.group(1)))
    for text in pages:
        for m in _PGN_BARE.finditer(text):
            add(int(m.group(1)))
    return out[:MAX_CANDIDATES]


def skeleton(pgns):
    return {"PGNs": [
        {"PGN": n, "Name": f"PGN {n}", "FastPacket": False,
         "Fields": [{"Name": "Field 1", "BitOffset": 0, "BitLength": 8,
                     "Resolution": 1, "Units": ""}]}
        for n in pgns]}


def extract(path):
    from pypdf import PdfReader          # import inside the sandboxed worker
    reader = PdfReader(path)
    pages = []
    for page in reader.pages[:MAX_PAGES]:
        try:
            text = page.extract_text() or ""
        except Exception:                # noqa: BLE001 — hostile input
            text = "[page extraction failed]"
        pages.append(text[:MAX_PAGE_CHARS])
    return {"pages": pages, "skeleton": skeleton(candidates(pages))}


def main(argv):
    if len(argv) != 2:
        print("usage: python3 -m kilodash.pdfextract <pdf>", file=sys.stderr)
        return 2
    try:
        result = extract(argv[1])
    except Exception as e:               # noqa: BLE001 — report, don't trace
        print(f"extraction failed: {e}", file=sys.stderr)
        return 1
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
