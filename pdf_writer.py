"""
Iterative, minimal, PDF writer.

Its goal is to be as "iterative" as possible, holding as little in memory as 
possible at any time, and yielding bytes as soon as they are available.
"""

import zlib
from collections.abc import Generator, Iterable, Sequence
from dataclasses import dataclass
from itertools import accumulate, chain, islice, pairwise
from times_kerning import KERNS

HEADER = b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n"
PAGE = (
    b"%d 0 obj <</Type /Page /Parent 3 0 R /MediaBox [0 0 %d %d] "
    b"/Contents %d 0 R /Resources 2 0 R>> endobj\n"
)
RESOURCES = (
    b"2 0 obj <<"
    b"/Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Times-Roman >> >> >>"
    b"endobj\n"
)
CATALOG = b"1 0 obj <</Type /Catalog /Pages 3 0 R >> endobj\n"
STREAM_START = b"%d 0 obj <</Length %d 0 R /Filter /FlateDecode>> stream\n"
STREAM_END = b"endstream endobj\n"


@dataclass
class Page:
    size: tuple[int, int]
    lines: Iterable[str]


def write_pdf(pages: Iterable[Page]) -> Iterable[bytes]:
    yield HEADER
    sizes = [len(HEADER)]
    # NOTE: `i` is always bound since PDF requires at least one page
    for i, p in enumerate(pages):
        pid = i * 3 + 4  # 3 objects per page, starting at ID 4
        yield (page := PAGE % (pid, *p.size, pid + 1))
        sizes.append(len(page))
        sizes.extend((yield from write_content(pid + 1, p.lines)))
    yield CATALOG
    sizes.append(len(CATALOG))
    yield RESOURCES
    sizes.append(len(RESOURCES))
    sizes.append((yield from gen_size(write_pnode(i + 1))))
    yield from write_xref(sizes)


def write_pnode(n_pages: int) -> Iterable[bytes]:
    yield b"3 0 obj << /Type /Pages /Kids ["
    yield from map(b"%d 0 R ".__mod__, range(4, n_pages * 3 + 4, 3))
    yield b"] /Count %d >> endobj\n" % n_pages


def write_content(
    id_: int, lines: Iterable[str]
) -> Generator[bytes, None, tuple[int, int]]:
    """
    Write the PDF content stream for a page.
    It writes 2 PDF objects! the stream object and the deferred length object.
    The lengths of these objects are returned as a tuple.
    """
    yield (stream_start := STREAM_START % (id_, id_ + 1))
    stream_size = yield from gen_size(
        deflate(
            chain(
                [b"BT /F1 12 Tf 72 770 Td 18 TL "],
                chain.from_iterable(map(write_line, lines)),
                [b"ET"],
            )
        )
    )
    yield STREAM_END
    yield (len_obj := b"%d 0 obj %d endobj\n" % (id_ + 1, stream_size))
    return (len(stream_start) + stream_size + len(STREAM_END), len(len_obj))


def write_line(s: str) -> Iterable[bytes]:
    yield b"[("
    for a, b in pairwise(s):
        yield _escape(a).encode("latin-1")
        if k := KERNS.get((a, b)):
            yield b") %d (" % -k
    if s:
        yield _escape(s[-1]).encode("latin-1")
    yield b")] TJ\nT*\n"


# NOTE: typical data volumes (<4kB) will only result in 1 chunk of data yielded at the end.
#       However, this iterative approach still prevents big memory spikes if the content is large,
#       and it demonstrates how to use DEFLATE in a streaming way.
def deflate(s: Iterable[bytes]) -> Iterable[bytes]:
    """Stream-compress using raw DEFLATE, yielding output chunks as they become available."""
    comp = zlib.compressobj(wbits=9, memLevel=1)
    yield from map(comp.compress, s)
    yield comp.flush()


def gen_size(it: Iterable[bytes]) -> Generator[bytes, None, int]:
    """Pass bytes through; return total byte count."""
    total = 0
    for chunk in it:
        total += len(chunk)
        yield chunk
    return total


# object sizes passed in ID order, EXCEPT 1-3 (which are last)
def write_xref(sizes: Sequence[int]) -> Iterable[bytes]:
    offsets = accumulate(sizes)
    n_var_objs = len(sizes) - 4
    yield b"xref\n0 1\n0000000000 65535 f \n"
    # write objects 4+ first
    yield b"4 %d\n" % n_var_objs
    yield from map(b"%010d 00000 n \n".__mod__, islice(offsets, n_var_objs))
    # then, write objects 1-3 (catalog, resources, page node) last
    yield b"1 3\n"
    yield from map(b"%010d 00000 n \n".__mod__, islice(offsets, 3))
    yield b"trailer <</Size %d /Root 1 0 R>>" % len(sizes)
    yield b"startxref %d %%%%EOF" % next(offsets)  # xref starts after


def _escape(c: str) -> str:
    return _ESCAPES.get(c, c)


_ESCAPES = {
    "\\": "\\\\",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\b": "\\b",
    "\f": "\\f",
    "(": "\\(",
    ")": "\\)",
}


if __name__ == "__main__":
    import sys

    A4 = (595, 842)

    pages = [
        Page(
            A4,
            [
                '     "To the man who loves art for its',
                'own sake," remarked Sherlock Holmes,',
                "tossing aside the advertisement sheet of",
                'The Daily Telegraph, "it is frequently in',
                "its least important and lowliest manifes-",
                "tations that the keenest pleasure is to be",
                "derived. It is pleasant to me to observe,",
                "Watson, that you have so far grasped this",
                "truth that in these little records of our",
            ],
        ),
        Page(
            A4,
            [
                "Page two: taking iterators too far.",
                "DEFLATE is a streaming algorithm by design.",
                "You can always go from lazy to eager.",
                "You cannot go the other way. :)",
            ],
        ),
    ]

    output_path = sys.argv[1] if len(sys.argv) > 1 else "output.pdf"
    with open(output_path, "wb") as f:
        f.writelines(write_pdf(pages))
    print(f"Written to {output_path}")
