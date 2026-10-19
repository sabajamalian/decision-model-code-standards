from __future__ import annotations

import textwrap

from standards_checker.chunker import Chunk, chunk_python

SOURCE = textwrap.dedent(
    '''\
    """Module docstring."""

    import os

    TIMEOUT = int(os.environ.get("TIMEOUT", "5"))


    @decorator
    def first(a):
        """First."""
        return a


    class Thing:
        """A thing."""

        def method(self):
            return 1


    async def second():
        return 2
    '''
)


def test_chunks_follow_top_level_definitions() -> None:
    chunks = chunk_python("pkg/mod.py", SOURCE)
    assert [c.symbol for c in chunks] == ["module-level code", "function first", "class Thing", "async function second"]
    first = chunks[1]
    assert (first.start_line, first.end_line) == (8, 11)
    assert first.lines[0] == (8, "@decorator")
    module = chunks[0]
    assert [n for n, _ in module.lines] == [1, 3, 5]


def test_trivial_module_code_is_skipped() -> None:
    source = '"""Doc."""\nimport os\nfrom x import y\n\n\ndef f():\n    return os, y\n'
    assert [c.symbol for c in chunk_python("m.py", source)] == ["function f"]


def test_large_class_is_split_by_method() -> None:
    body = "\n".join(f"        x{i} = {i}" for i in range(10))
    source = f"class Big:\n    attr = 1\n\n    def a(self):\n{body}\n\n    def b(self):\n{body}\n"
    symbols = [c.symbol for c in chunk_python("m.py", source, max_lines=12)]
    assert symbols == ["method Big.a", "method Big.b", "class Big"]


def test_long_function_is_windowed() -> None:
    body = "\n".join(f"    x{i} = {i}" for i in range(9))
    source = f"def long():\n{body}\n"
    chunks = chunk_python("m.py", source, max_lines=4)
    assert [c.symbol for c in chunks] == [
        "function long (part 1/3)",
        "function long (part 2/3)",
        "function long (part 3/3)",
    ]
    assert chunks[-1].end_line == 10


def test_unparsable_file_falls_back_to_line_windows() -> None:
    chunks = chunk_python("broken.py", "def broken(:\n    pass\n", max_lines=150)
    assert len(chunks) == 1
    assert chunks[0].symbol == "file (unparsed)"


def test_empty_file_has_no_chunks() -> None:
    assert chunk_python("empty.py", "") == []


def test_render_and_overlaps() -> None:
    chunk = Chunk("pkg/mod.py", "function f", ((9, "def f():"), (10, "    return 1")))
    rendered = chunk.render("Flask API")
    assert rendered.splitlines()[:3] == [
        "Project context: Flask API",
        "File: pkg/mod.py",
        "Code unit: function f (lines 9-10)",
    ]
    assert "   9 | def f():" in rendered
    assert "Project context" not in chunk.render()
    assert chunk.overlaps({10, 50})
    assert not chunk.overlaps({1, 11})
