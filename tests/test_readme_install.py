"""The README's install lines must be runnable as written.

This project is deliberately not published to PyPI: README line 17 installs it from git. The
`Paying` section two sections later said `pip install "gpt-researcher-x402-retriever[feeless402]"`,
which is the same name without the git URL, so the first command an agent runs to become *able to
pay* answered:

    ERROR: Could not find a version that satisfies the requirement
           gpt-researcher-x402-retriever[feeless402] (from versions: none)

Checked offline and with no network. Every install command in a fenced code block (`pip`, `pip3`,
`python -m pip`, `python3 -m pip`, `uv pip`, optionally after `$ ` or `sudo `, with backslash
continuations joined) is split like a shell would and each requirement is judged on its own: one
whose PEP 503 name is in `NOT_ON_PYPI` must be a direct reference to *that* project's repository
under github.com/dhyabi2, and `-e` must be given a path or a VCS URL, never a bare name. Prose is
not judged: only fenced code blocks are scanned.
"""

import pathlib
import re
import shlex

import pytest

DIST = "gpt-researcher-x402-retriever"
#: Projects of ours that are NOT on PyPI, measured 2026-10-13: both /pypi/<name>/json were 404.
#: Each maps to its repository name under github.com/dhyabi2.
NOT_ON_PYPI = {DIST: DIST, "openai-agents-nano-x402": "openai-agents-nano-x402"}
OWNER = "dhyabi2"
README = pathlib.Path(__file__).resolve().parent.parent / "README.md"

_FENCE = re.compile(r"^\s*(```|~~~)")
_INSTALL = re.compile(
    r"^\s*(?:\$\s+)?(?:sudo\s+)?"
    r"(?:pip3?|python(?:3(?:\.\d+)?)?\s+-m\s+pip|uv\s+pip)"
    r"\s+install(?:\s+(?P<args>.*))?$"
)
_NAME = re.compile(r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(?:\[[^\]]*\])?(?P<rest>.*)$", re.S)
_VCS = re.compile(r"^(?:git|hg|svn|bzr)\+[a-z]+://", re.I)
_URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
#: Options whose value is the next argument and is not a requirement.
_TAKES_VALUE = {
    "-r", "--requirement", "-c", "--constraint", "-i", "--index-url", "--extra-index-url",
    "-f", "--find-links", "-t", "--target", "--prefix", "--root", "--src", "--upgrade-strategy",
    "--python", "-p", "--platform", "--python-version", "--implementation", "--abi",
    "--trusted-host", "--cache-dir", "--log", "--proxy", "--timeout", "--retries",
    "--global-option", "--config-settings", "-C", "--report", "--progress-bar",
}


def canonical(name):
    """PEP 503 normalisation: lowercase, and runs of `-`, `_`, `.` become one `-`."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _ours(url, repo):
    """True for a URL that points at github.com/dhyabi2/<repo> itself (git, archive, codeload)."""
    url = re.sub(r"^git\+", "", url, flags=re.I)
    return re.match(
        r"^https://(?:www\.)?(?:github\.com|codeload\.github\.com)/%s/%s(?:\.git)?(?:[/@#?].*)?$"
        % (re.escape(OWNER), re.escape(repo)),
        url,
        re.I,
    ) is not None


def _is_path(arg):
    return arg.startswith((".", "/", "~")) or "/" in arg or "\\" in arg or arg.lower().startswith("file:")


def install_commands(markdown):
    """The argument strings of every install command inside a fenced code block."""
    found, in_code, pending = [], False, ""
    for line in markdown.splitlines():
        if _FENCE.match(line):
            in_code, pending = not in_code, ""
            continue
        if not in_code:
            continue
        if line.rstrip().endswith("\\"):
            pending += line.rstrip()[:-1] + " "
            continue
        line, pending = pending + line, ""
        match = _INSTALL.match(line)
        if match:
            found.append(match.group("args") or "")
    return found


def _split(args):
    lexer = shlex.shlex(args, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""  # `#` mid-word is a URL fragment (#egg=, #subdirectory=), not a comment
    tokens = []
    for token in lexer:
        if token.startswith("#"):
            break
        tokens.append(token)
    # An unquoted `name @ url` is three shell words; put the requirement back together.
    merged = []
    for token in tokens:
        if merged and (token.startswith("@") or merged[-1].endswith("@")):
            merged[-1] += " " + token
        else:
            merged.append(token)
    return merged


def problems(args):
    """Why this install command cannot run as written; empty when every requirement is fine."""
    found = []
    tokens = _split(args)
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if token in ("-e", "--editable"):
            if i >= len(tokens):
                found.append("`-e` with nothing after it")
                continue
            target = tokens[i]
            i += 1
            if not (_is_path(target) or _VCS.match(target)):
                found.append("`-e %s` is a bare name; -e takes a path or a VCS URL" % target)
            else:
                found.extend(_judge(target))
            continue
        if token.startswith("--editable="):
            target = token.split("=", 1)[1]
            if not (_is_path(target) or _VCS.match(target)):
                found.append("`%s` is a bare name; -e takes a path or a VCS URL" % token)
            continue
        if token.startswith("-"):
            if token in _TAKES_VALUE:
                i += 1
            continue
        found.extend(_judge(token))
    return found


def _judge(req):
    """One requirement: a name of ours must carry a direct reference to its own repository."""
    if _URL.match(req) or _is_path(req.split(" @", 1)[0].split("[", 1)[0]):
        # A bare URL or path names no project; pip fetches exactly that.
        return []
    match = _NAME.match(req)
    if not match:
        return []
    name = canonical(match.group("name"))
    if name not in NOT_ON_PYPI:
        return []
    rest = match.group("rest").strip()
    if not rest.startswith("@"):
        return ["`%s` names %s, which is not on PyPI, with no direct reference" % (req, name)]
    url = rest[1:].split(";", 1)[0].strip()
    if not _ours(url, NOT_ON_PYPI[name]):
        return ["`%s` points %s at %s, not github.com/%s/%s" % (req, name, url, OWNER, NOT_ON_PYPI[name])]
    return []


def test_the_readme_has_install_lines_to_check():
    assert install_commands(README.read_text(encoding="utf-8")), (
        "no install command found in a code block; this test would pass vacuously"
    )


def test_no_install_line_resolves_one_of_ours_against_pypi():
    for args in install_commands(README.read_text(encoding="utf-8")):
        assert not problems(args), "README install `%s` cannot run as written: %s" % (args, problems(args))


def test_the_not_on_pypi_names_are_still_mentioned():
    """Drop a name from NOT_ON_PYPI only once the README stops mentioning it."""
    text = README.read_text(encoding="utf-8")
    for name in NOT_ON_PYPI:
        assert name in text


GIT = "git+https://github.com/dhyabi2/gpt-researcher-x402-retriever"

BAD = [
    '```\npython -m pip install "gpt-researcher-x402-retriever[feeless402]"\n```',
    "```\npython3 -m pip install gpt-researcher-x402-retriever\n```",
    "```\n$ sudo pip3 install gpt_researcher_x402_retriever\n```",
    "```\nuv pip install GPT.Researcher-X402_retriever\n```",
    "```\npip install -e gpt-researcher-x402-retriever\n```",
    '```\npip install gpt-researcher-x402-retriever "feeless402 @ %s"\n```' % GIT,
    '```\npip install "gpt-researcher-x402-retriever @ git+https://github.com/someone/gpt-researcher-x402-retriever"\n```',
    '```\npip install "gpt-researcher-x402-retriever @ git+https://github.com/dhyabi2/openai-agents-nano-x402"\n```',
    "```\npip install --upgrade \\\n    gpt-researcher-x402-retriever\n```",
    "```\npip install openai-agents-nano-x402  # %s\n```" % GIT,
]

GOOD = [
    '```bash\npip install "gpt-researcher-x402-retriever[feeless402] @ %s"\n```' % GIT,
    '```\npip install "%s"\n```' % GIT,
    "```\npip install gpt-researcher-x402-retriever @ %s@main\n```" % GIT,
    '```\npip install "gpt-researcher-x402-retriever @ '
    'https://github.com/dhyabi2/gpt-researcher-x402-retriever/archive/refs/heads/main.zip"\n```',
    '```\npip install -e ".[test]"\n```',
    "```\npip install -e %s#egg=gpt-researcher-x402-retriever\n```" % GIT,
    "```\npip install feeless402 requests\n```",
    "```\npython3 -m pip install -r requirements.txt \\\n  \"openai-agents-nano-x402 @ "
    'git+https://github.com/dhyabi2/openai-agents-nano-x402"\n```',
]


@pytest.mark.parametrize("markdown", BAD)
def test_the_checker_refuses_a_wrong_install_line(markdown):
    commands = install_commands(markdown)
    assert commands, "the checker did not even see the command"
    assert any(problems(args) for args in commands)


@pytest.mark.parametrize("markdown", GOOD)
def test_the_checker_accepts_a_right_install_line(markdown):
    commands = install_commands(markdown)
    assert commands, "the checker did not even see the command"
    assert not any(problems(args) for args in commands), [problems(a) for a in commands]


def test_prose_is_not_judged():
    markdown = 'Do not run `pip install gpt-researcher-x402-retriever`; it is not on PyPI.\n' \
               "pip install gpt-researcher-x402-retriever\n"
    assert install_commands(markdown) == []
