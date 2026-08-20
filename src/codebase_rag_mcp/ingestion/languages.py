"""Extension-based language detection.

Static mapping only -- no Tree-sitter dependency here (that lands in
Day 03's parser module). Anything unrecognized falls back to "unknown"
rather than raising, since language detection must never block a scan.
"""

from __future__ import annotations

from pathlib import Path

UNKNOWN_LANGUAGE = "unknown"

EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".md": "markdown",
    ".mdx": "markdown",
    ".rst": "restructuredtext",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".sql": "sql",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".proto": "protobuf",
}


def detect_language(path: Path) -> str:
    """Return the language for `path`'s suffix, or "unknown" if unmapped."""
    return EXTENSION_LANGUAGE_MAP.get(path.suffix.lower(), UNKNOWN_LANGUAGE)


__all__ = ["EXTENSION_LANGUAGE_MAP", "UNKNOWN_LANGUAGE", "detect_language"]
