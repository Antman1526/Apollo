"""The one-shot `python` tool echoes a trailing bare expression like a REPL."""
import subprocess
import sys

from src.tool_execution import echo_last_expression


def _run(code: str) -> str:
    return subprocess.run([sys.executable, "-I", "-c", echo_last_expression(code)],
                          capture_output=True, text=True, timeout=30).stdout


def test_bare_expression_is_echoed():
    assert _run("48271 * 9973") == "481406683\n"


def test_print_last_is_not_echoed_twice():
    assert _run("print(2 + 2)") == "4\n"


def test_assignment_last_prints_nothing():
    assert _run("x = 5") == ""


def test_multiline_trailing_expression():
    code = "import math\nmath.sqrt(\n    16\n)"
    assert _run(code) == "4.0\n"


def test_strings_echo_with_repr():
    assert _run("s = 'hi'\ns.upper()") == "'HI'\n"


def test_syntax_error_is_left_to_the_interpreter():
    code = "def broken(:\n"
    assert echo_last_expression(code) == code


def test_semicolon_statements_on_the_same_line_are_kept():
    assert _run("x = 1; x") == "1\n"
    assert _run('print("side"); 99') == "side\n99\n"


def test_trailing_comment_does_not_break_echo():
    assert _run("y = 3\ny * 2  # double it") == "6\n"
