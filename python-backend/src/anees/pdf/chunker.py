"""Batch chunking for the processing pipeline (Issue #10)."""

CHUNK_SIZE = 10


class PDFChunker:
    """Split a PDF's pages into fixed-size, zero-based half-open batches."""

    def __init__(self, chunk_size: int = CHUNK_SIZE) -> None:
        self.chunk_size = chunk_size

    def split(self, total_pages: int) -> list[tuple[int, int]]:
        """Return ``[start, end)`` page ranges, one per 10-page batch."""
        return [
            (start, min(start + self.chunk_size, total_pages))
            for start in range(0, total_pages, self.chunk_size)
        ]
