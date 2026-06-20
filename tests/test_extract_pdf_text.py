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
    monkeypatch.setattr(pdf_module, "extract_page_markdown", lambda page: ("ok content", False, 0))

    md, likely_scanned, n_tables = pdf_module._extract_page_markdown_with_timeout(object(), page_num=1)

    assert md == "ok content"
    assert likely_scanned is False
    assert n_tables == 0
