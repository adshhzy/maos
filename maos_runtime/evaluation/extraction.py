"""Extract generated files from Markdown-like Agent output."""

from __future__ import annotations

import re
from dataclasses import dataclass


FENCE_RE = re.compile(
    r"```(?P<info>[^\n`]*)\n(?P<body>.*?)```",
    re.DOTALL,
)


@dataclass(frozen=True)
class ExtractedFiles:
    files: dict[str, str]
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def extract_python_files(
    output: str,
    *,
    required_files: tuple[str, ...],
) -> ExtractedFiles:
    """Extract named Python files from Agent output.

    The extractor is intentionally deterministic. It does not ask an LLM to
    infer missing files. It first looks for code fences near a filename mention,
    then falls back to ordered Python fences.
    """

    text = output or ""
    fences = list(_iter_fences(text))
    files: dict[str, str] = {}
    for filename in required_files:
        match = _find_fence_for_filename(text, fences, filename)
        if match:
            files[filename] = _strip_code(match)

    remaining = [name for name in required_files if name not in files]
    python_fences = [
        body
        for info, body, _start in fences
        if _is_python_fence(info) and _looks_like_python(body)
    ]
    for filename, body in zip(remaining, python_fences):
        if filename not in files:
            files[filename] = _strip_code(body)

    errors = [
        f"Missing required generated file: {filename}"
        for filename in required_files
        if filename not in files or not files[filename].strip()
    ]
    return ExtractedFiles(files=files, errors=errors)


def _iter_fences(text: str) -> list[tuple[str, str, int]]:
    return [
        (match.group("info").strip(), match.group("body"), match.start())
        for match in FENCE_RE.finditer(text)
    ]


def _find_fence_for_filename(
    text: str,
    fences: list[tuple[str, str, int]],
    filename: str,
) -> str | None:
    lowered = filename.lower()
    for info, body, start in fences:
        prefix = text[max(0, start - 500) : start].lower()
        info_lower = info.lower()
        if lowered in info_lower or lowered in prefix:
            return body
    return None


def _is_python_fence(info: str) -> bool:
    value = info.lower()
    return not value or "python" in value or value in {"py"}


def _looks_like_python(body: str) -> bool:
    sample = body[:2000]
    markers = ("def ", "class ", "import ", "from ", "async def ", "@")
    return any(marker in sample for marker in markers)


def _strip_code(body: str) -> str:
    return body.strip().replace("\r\n", "\n") + "\n"
