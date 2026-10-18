"""Batch chunker tests (Issue #10).

Stdlib-only: CI's Python job installs just ruff+pytest, so these run there.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from anees.pdf.chunker import CHUNK_SIZE, PDFChunker


def test_default_chunk_size_is_ten() -> None:
    assert CHUNK_SIZE == 10
    assert PDFChunker().chunk_size == 10


def test_split_87_pages_into_nine_batches() -> None:
    batches = PDFChunker().split(87)

    assert len(batches) == 9
    assert batches[0] == (0, 10)
    assert batches[7] == (70, 80)
    assert batches[8] == (80, 87)
    assert sum(end - start for start, end in batches) == 87


def test_split_last_batch_is_smaller() -> None:
    assert PDFChunker().split(15) == [(0, 10), (10, 15)]


def test_split_exact_multiple_has_no_trailing_empty_batch() -> None:
    assert PDFChunker().split(20) == [(0, 10), (10, 20)]


def test_split_zero_pages_returns_no_batches() -> None:
    assert PDFChunker().split(0) == []
