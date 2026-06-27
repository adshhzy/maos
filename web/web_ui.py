"""Web UI template loader."""

from pathlib import Path


def load_index_html() -> str:
    return (Path(__file__).resolve().parent / "static" / "index.html").read_text(
        encoding="utf-8"
    )


INDEX_HTML = load_index_html()
