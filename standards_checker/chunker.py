"""Split Python source into function/class-sized chunks with original line numbers."""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Chunk:
    """A unit of code sent to Decision-1 as one `state`."""

    path: str
    symbol: str
    lines: tuple[tuple[int, str], ...]

    @property
    def start_line(self) -> int:
        """First source line number in the chunk."""
        return self.lines[0][0]

    @property
    def end_line(self) -> int:
        """Last source line number in the chunk."""
        return self.lines[-1][0]

    def overlaps(self, line_numbers: set[int]) -> bool:
        """True if any line of this chunk is in `line_numbers`."""
        return any(number in line_numbers for number, _ in self.lines)

    def render(self, context: str = "") -> str:
        """Format the chunk as the Decision-1 `state` text (header plus numbered code)."""
        header = []
        if context:
            header.append(f"Project context: {context.strip()}")
        header.append(f"File: {self.path}")
        header.append(f"Code unit: {self.symbol} (lines {self.start_line}-{self.end_line})")
        body = "\n".join(f"{number:>4} | {text}" for number, text in self.lines)
        return "\n".join(header) + "\n\n" + body


def _first_line(node: ast.stmt) -> int:
    decorators = getattr(node, "decorator_list", None) or []
    return min([node.lineno, *(d.lineno for d in decorators)])


def _is_trivial(node: ast.stmt) -> bool:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return True
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _make_chunks(
    path: str, symbol: str, numbers: Iterable[int], source_lines: Sequence[str], max_lines: int
) -> list[Chunk]:
    numbered = [(n, source_lines[n - 1]) for n in sorted(set(numbers)) if 0 < n <= len(source_lines)]
    while numbered and not numbered[-1][1].strip():
        numbered.pop()
    if not numbered:
        return []
    windows = [numbered[i : i + max_lines] for i in range(0, len(numbered), max_lines)]
    if len(windows) == 1:
        return [Chunk(path, symbol, tuple(windows[0]))]
    return [
        Chunk(path, f"{symbol} (part {i}/{len(windows)})", tuple(window)) for i, window in enumerate(windows, start=1)
    ]


def chunk_python(path: str, source: str, max_lines: int = 150) -> list[Chunk]:
    """Return one chunk per top-level function/class plus one for meaningful module-level code.

    Classes longer than `max_lines` are split into one chunk per method plus a chunk for the
    remaining class body. Files that do not parse are split into fixed-size line windows.
    """
    source_lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _make_chunks(path, "file (unparsed)", range(1, len(source_lines) + 1), source_lines, max_lines)

    chunks: list[Chunk] = []
    module_lines: list[int] = []
    module_meaningful = False

    for node in tree.body:
        start, end = _first_line(node), node.end_lineno or node.lineno
        if isinstance(node, _FUNCS):
            kind = "async function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            chunks += _make_chunks(path, f"{kind} {node.name}", range(start, end + 1), source_lines, max_lines)
        elif isinstance(node, ast.ClassDef):
            if end - start + 1 <= max_lines:
                chunks += _make_chunks(path, f"class {node.name}", range(start, end + 1), source_lines, max_lines)
                continue
            method_lines: set[int] = set()
            for child in node.body:
                if isinstance(child, _FUNCS):
                    c_start, c_end = _first_line(child), child.end_lineno or child.lineno
                    method_lines.update(range(c_start, c_end + 1))
                    chunks += _make_chunks(
                        path, f"method {node.name}.{child.name}", range(c_start, c_end + 1), source_lines, max_lines
                    )
            rest = [n for n in range(start, end + 1) if n not in method_lines]
            chunks += _make_chunks(path, f"class {node.name}", rest, source_lines, max_lines)
        else:
            module_lines.extend(range(start, end + 1))
            module_meaningful = module_meaningful or not _is_trivial(node)

    if module_meaningful:
        chunks = _make_chunks(path, "module-level code", module_lines, source_lines, max_lines) + chunks
    return chunks
