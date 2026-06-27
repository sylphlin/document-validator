# tests/test_extract_pdf_text.py
import importlib.util
import time
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parent.parent / "skill" / "scripts" / "extract_pdf_text.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("extract_pdf_text", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pdf_module():
    return _load_module()


class _WrappedError(Exception):
    """Simulates pdfplumber's PdfminerException, which catches *any* exception
    raised during lazy layout parsing — including our SIGALRM-raised
    _PageTimeoutError — and re-raises it as a different exception type."""


def test_timeout_is_detected_even_when_wrapped_by_another_exception_type(pdf_module, monkeypatch):
    monkeypatch.setattr(pdf_module, "PAGE_TIMEOUT_SECONDS", 1)

    class SlowWrappingPage:
        def __getattr__(self, name):
            # Mirrors pdfplumber/page.py's `layout` property: it wraps the
            # SIGALRM-raised _PageTimeoutError that interrupts time.sleep()
            # into a different exception type (PdfminerException in real
            # pdfplumber), which is the actual production crash.
            try:
                time.sleep(1.5)
            except Exception as e:
                raise _WrappedError(e)
            raise _WrappedError("page finished without being interrupted")

    md, likely_scanned, n_tables = pdf_module._extract_page_markdown_with_timeout(
        SlowWrappingPage(), page_num=1
    )

    assert "timed out" in md
    assert likely_scanned is True
    assert n_tables == 0


def test_non_timeout_exceptions_still_propagate(pdf_module, monkeypatch):
    monkeypatch.setattr(pdf_module, "PAGE_TIMEOUT_SECONDS", 30)

    class BrokenPage:
        def __getattr__(self, name):
            raise _WrappedError("a genuine parsing failure, not a timeout")

    with pytest.raises(_WrappedError):
        pdf_module._extract_page_markdown_with_timeout(BrokenPage(), page_num=1)


def test_page_finishing_well_within_timeout_is_unaffected(pdf_module, monkeypatch):
    monkeypatch.setattr(pdf_module, "PAGE_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(pdf_module, "extract_page_markdown", lambda page, pdfium_page=None: ("ok content", False, 0))

    md, likely_scanned, n_tables = pdf_module._extract_page_markdown_with_timeout(object(), page_num=1)

    assert md == "ok content"
    assert likely_scanned is False
    assert n_tables == 0


class _FakePdfiumObj:
    def __init__(self, obj_type):
        self.type = obj_type


class _FakePdfiumTextPage:
    def __init__(self, text):
        self._text = text

    def get_text_range(self):
        return self._text


class _FakePdfiumPage:
    def __init__(self, objects, text=""):
        self._objects = objects
        self._text = text

    def get_objects(self):
        return iter(self._objects)

    def get_textpage(self):
        return _FakePdfiumTextPage(self._text)


def test_is_drawing_page_stops_counting_once_past_threshold(pdf_module):
    # 1000 path objects with threshold 800 — early exit means we never need to
    # iterate all 1000; the reported count just needs to be > threshold.
    objects = [_FakePdfiumObj(pdf_module.PDFIUM_PATH_OBJECT_TYPE) for _ in range(1000)]
    is_drawing, count = pdf_module._is_drawing_page(_FakePdfiumPage(objects), threshold=800)
    assert is_drawing is True
    assert count == 801


def test_is_drawing_page_false_under_threshold(pdf_module):
    objects = [_FakePdfiumObj(pdf_module.PDFIUM_PATH_OBJECT_TYPE) for _ in range(5)]
    is_drawing, count = pdf_module._is_drawing_page(_FakePdfiumPage(objects), threshold=800)
    assert is_drawing is False
    assert count == 5


def test_is_drawing_page_ignores_non_path_objects(pdf_module):
    # Text/image objects shouldn't count toward the vector-density threshold.
    objects = [_FakePdfiumObj(1) for _ in range(2000)]  # type 1 == text, not path
    is_drawing, count = pdf_module._is_drawing_page(_FakePdfiumPage(objects), threshold=800)
    assert is_drawing is False
    assert count == 0


class _UntouchedPdfplumberPage:
    def __getattr__(self, name):
        raise AssertionError(f"pdfplumber page.{name} should not be touched on a drawing page")


def test_extract_page_markdown_drawing_page_with_real_text_is_not_likely_scanned(pdf_module, monkeypatch):
    # A drawing page with a substantial recovered paragraph is NOT "no usable
    # content" — likely_scanned should reflect actual text presence, not just
    # "this is a drawing page" (which used to be hardcoded True regardless).
    monkeypatch.setattr(pdf_module, "DRAWING_PAGE_VECTOR_THRESHOLD", 800)
    objects = [_FakePdfiumObj(pdf_module.PDFIUM_PATH_OBJECT_TYPE) for _ in range(900)]
    long_text = "基地面積7354.95平方公尺，使用分區為第一種產業專用區，建蔽率60%，容積率490%。"
    pdfium_page = _FakePdfiumPage(objects, text=long_text)

    md, likely_scanned, n_tables = pdf_module.extract_page_markdown(_UntouchedPdfplumberPage(), pdfium_page)

    assert long_text in md
    assert "technical drawing" in md  # the inline annotation still notes the unanalyzed diagram
    assert likely_scanned is False
    assert n_tables == 0


def test_extract_page_markdown_drawing_page_with_no_text_is_likely_scanned(pdf_module, monkeypatch):
    monkeypatch.setattr(pdf_module, "DRAWING_PAGE_VECTOR_THRESHOLD", 800)
    objects = [_FakePdfiumObj(pdf_module.PDFIUM_PATH_OBJECT_TYPE) for _ in range(900)]
    pdfium_page = _FakePdfiumPage(objects, text="")

    md, likely_scanned, n_tables = pdf_module.extract_page_markdown(_UntouchedPdfplumberPage(), pdfium_page)

    assert "technical drawing" in md
    assert likely_scanned is True
    assert n_tables == 0


def test_extract_page_markdown_falls_through_to_pdfplumber_when_not_drawing(pdf_module, monkeypatch):
    monkeypatch.setattr(pdf_module, "DRAWING_PAGE_VECTOR_THRESHOLD", 800)
    pdfium_page = _FakePdfiumPage([_FakePdfiumObj(pdf_module.PDFIUM_PATH_OBJECT_TYPE) for _ in range(3)])
    monkeypatch.setattr(
        pdf_module, "_is_drawing_page",
        lambda page, threshold: (False, 3) if page is pdfium_page else (True, 9999),
    )

    class _FakePdfplumberPage:
        lines = []
        curves = []
        rects = []
        images = []

        def find_tables(self, table_settings=None):
            return []

        def extract_text(self):
            return "normal paragraph text"

        def filter(self, fn):
            return self

    md, likely_scanned, n_tables = pdf_module.extract_page_markdown(_FakePdfplumberPage(), pdfium_page)

    assert md == "normal paragraph text"
    assert likely_scanned is False
    assert n_tables == 0
