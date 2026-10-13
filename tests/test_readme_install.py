"""The README's install lines must be runnable as written.

This project is deliberately not published to PyPI: README line 17 installs it from git. The
`Paying` section two sections later said `pip install "gpt-researcher-x402-retriever[feeless402]"`,
which is the same name without the git URL, so the first command an agent runs to become *able to
pay* answered:

    ERROR: Could not find a version that satisfies the requirement
           gpt-researcher-x402-retriever[feeless402] (from versions: none)

Checked offline and with no network: a `pip install` line that names this project must carry a
direct reference (PEP 508 `name[extras] @ url`, or a bare `git+`/`-e` form), because a bare name
resolves against PyPI, where this project is not.
"""

import pathlib
import re

DIST = "gpt-researcher-x402-retriever"
#: Projects of ours that are NOT on PyPI, measured 2026-10-13: both /pypi/<name>/json were 404.
NOT_ON_PYPI = (DIST, "openai-agents-nano-x402")
README = pathlib.Path(__file__).resolve().parent.parent / "README.md"
_PIP_INSTALL = re.compile(r"^\s*pip install\s+(?P<args>.*)$", re.MULTILINE)


def _install_lines():
    text = README.read_text(encoding="utf-8")
    return [m.group("args").split("#", 1)[0].strip() for m in _PIP_INSTALL.finditer(text)]


def test_the_readme_has_install_lines_to_check():
    assert _install_lines(), "no `pip install` line found; this test would pass vacuously"


def test_no_pip_install_line_resolves_one_of_ours_against_pypi():
    for args in _install_lines():
        for name in NOT_ON_PYPI:
            if name not in args:
                continue
            assert ("@" in args) or ("git+" in args) or args.startswith("-e"), (
                "README `pip install %s` names %s, which is not on PyPI, with no direct "
                "reference: that command cannot run as written" % (args, name)
            )
