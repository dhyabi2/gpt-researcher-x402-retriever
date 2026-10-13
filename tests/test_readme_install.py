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
under github.com/dhyabi2, and `-e` must be given a path or a VCS URL, never a bare name. A bare
URL is judged by the repository it points at and by its `#egg=`; extras asked of this project must
exist in pyproject.toml, and the Paying line must keep `[feeless402]`. Prose is not judged: only
fenced code blocks are scanned.
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
ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
PYPROJECT = ROOT / "pyproject.toml"
#: The README section whose install line makes the retriever able to pay, and the extra it needs.
PAYING_HEADING = "openai-agents-nano-x402 / feeless402"
PAYING_EXTRA = "feeless402"

_FENCE = re.compile(r"^\s*(```|~~~)")
_INSTALL = re.compile(
    r"^\s*(?:\$\s+)?(?:sudo\s+)?"
    r"(?:pip3?|python(?:3(?:\.\d+)?)?\s+-m\s+pip|uv\s+pip)"
    r"\s+install(?:\s+(?P<args>.*))?$"
)
_NAME = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(?:\[(?P<extras>[^\]]*)\])?(?P<rest>.*)$", re.S
)
_GITHUB_REPO = re.compile(r"^(?:git\+)?https?://(?:www\.)?(?:codeload\.)?github\.com/([^/@#?]+)/([^/@#?]+)", re.I)
_EGG = re.compile(r"[#&]egg=([^&\s]+)")
_LOCAL_EXTRAS = re.compile(r"^\.(?:/)?\[(?P<extras>[^\]]*)\]$")
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
    """True only for a URL that installs github.com/dhyabi2/<repo> itself.

    Four forms, nothing else: ``git+https://github.com/dhyabi2/<repo>[.git][@ref]``, the same over
    ``git+ssh://git@github.com/...``, a GitHub
    ``/archive/....zip|.tar.gz`` of that repo, or its ``codeload.github.com`` equivalent; each may
    end in a ``#fragment``. No ``..``, ``//``, ``%`` or ``\\`` anywhere, so a path cannot walk back
    out of the repository (``/dhyabi2/<repo>/../../someone/evil``).
    """
    after_scheme = url.split("://", 1)[-1]
    if ".." in url or "%" in url or "\\" in url or "//" in after_scheme or re.search(r"\s", url):
        return False
    repo = re.escape(repo)
    owner = re.escape(OWNER)
    fragment = r"(?:#[^#]*)?"
    forms = (
        r"git\+https://(?:www\.)?github\.com/%s/%s(?:\.git)?/?(?:@[^@#?]+)?" % (owner, repo),
        r"git\+ssh://git@github\.com/%s/%s(?:\.git)?/?(?:@[^@#?]+)?" % (owner, repo),
        r"https://(?:www\.)?github\.com/%s/%s/archive/[^#?]+\.(?:zip|tar\.gz)" % (owner, repo),
        r"https://codeload\.github\.com/%s/%s/(?:legacy\.)?(?:zip|tar\.gz)/[^#?]+" % (owner, repo),
    )
    return any(re.fullmatch(form + fragment, url, re.I) for form in forms)


def declared_extras(text=None):
    """The extras pyproject.toml declares, PEP 503-normalised.

    `tomllib` is 3.11+; on 3.10 `tomli` is used if installed, else a small parse of the one
    `[project.optional-dependencies]` table (a self-test pins it to tomllib where both exist).
    """
    text = PYPROJECT.read_text(encoding="utf-8") if text is None else text
    try:
        import tomllib
    except ImportError:  # Python 3.10
        try:
            import tomli as tomllib
        except ImportError:
            return _declared_extras_fallback(text)
    return {canonical(k) for k in tomllib.loads(text).get("project", {}).get("optional-dependencies", {})}


def _declared_extras_fallback(text):
    section = re.search(r"^\[project\.optional-dependencies\][ \t]*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if not section:
        return set()
    return {canonical(k) for k in re.findall(r"^[ \t]*([A-Za-z0-9][A-Za-z0-9._-]*)[ \t]*=", section.group(1), re.M)}


def _check_extras(extras, where):
    if extras is None:
        return []
    known = declared_extras()
    return [
        "`%s` asks for extra [%s], which pyproject.toml does not declare" % (where, extra)
        for extra in (e.strip() for e in extras.split(","))
        if extra and canonical(extra) not in known
    ]


def _last_segment(url):
    """The URL path's last segment, without a `#fragment`, `?query`, `@ref`, trailing `/` or `.git`."""
    rest = re.split(r"[#?]", url, maxsplit=1)[0]
    if "://" not in rest:
        return None
    path = rest.split("://", 1)[1].partition("/")[2]  # drop the host (and any user@ in it)
    path = path.split("@", 1)[0].rstrip("/")
    return re.sub(r"\.git$", "", path.rsplit("/", 1)[-1], flags=re.I) or None


def _bare_url(url):
    """A URL with no `name @` in front: judge the project it names by its repo path or #egg=."""
    found, named = [], set()
    egg = _EGG.search(url)
    if egg:
        match = _NAME.match(egg.group(1))
        if match and canonical(match.group("name")) in NOT_ON_PYPI:
            named.add(canonical(match.group("name")))
            if canonical(match.group("name")) == DIST:
                found.extend(_check_extras(match.group("extras"), url))
    # Whatever the host or scheme (gitub.com, gitlab.com, htps://), a URL whose last path segment
    # is the name of one of ours must be that project's own repository.
    last = _last_segment(url)
    if last is not None and canonical(last) in {canonical(r): n for n, r in NOT_ON_PYPI.items()}:
        named.add({canonical(r): n for n, r in NOT_ON_PYPI.items()}[canonical(last)])
    repo_match = _GITHUB_REPO.match(url)
    if repo_match:
        owner, repo = repo_match.group(1), re.sub(r"\.git$", "", repo_match.group(2), flags=re.I)
        by_repo = {canonical(r): n for n, r in NOT_ON_PYPI.items()}
        if owner.lower() == OWNER.lower():
            if canonical(repo) not in by_repo:
                found.append("`%s` is under github.com/%s but names no project of ours" % (url, OWNER))
            else:
                named.add(by_repo[canonical(repo)])
        elif canonical(repo) in by_repo:
            named.add(by_repo[canonical(repo)])
    for name in sorted(named):
        if not _ours(url, NOT_ON_PYPI[name]):
            found.append("`%s` installs %s from somewhere other than github.com/%s/%s"
                         % (url, name, OWNER, NOT_ON_PYPI[name]))
    return found


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
    if _URL.match(req):
        return _bare_url(req)
    local = _LOCAL_EXTRAS.match(req)
    if local:  # `.[extras]` is this checkout, so its extras are ours to check
        return _check_extras(local.group("extras"), req)
    if _is_path(req.split(" @", 1)[0].split("[", 1)[0]):
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
    found = _bare_url(url)  # a #egg= naming a different project of ours is still wrong
    if name == DIST:
        found.extend(_check_extras(match.group("extras"), req))
    return found


def section_install_commands(markdown, heading):
    """Install commands in the code blocks under one heading (up to the next heading)."""
    lines, kept, in_code, inside = markdown.splitlines(), [], False, False
    for line in lines:
        if _FENCE.match(line):
            in_code = not in_code
        elif not in_code and line.startswith("#"):
            inside = line.lstrip("#").strip() == heading
            continue
        if inside:
            kept.append(line)
    return install_commands("\n".join(kept))


def carries_extra(args, extra):
    """True when some requirement on this line installs this project with `extra`."""
    for token in _split(args):
        match = _NAME.match(token)
        if not _URL.match(token) and match and canonical(match.group("name")) == DIST:
            if extra in {canonical(e.strip()) for e in (match.group("extras") or "").split(",")}:
                return True
    return False


def test_the_readme_has_install_lines_to_check():
    assert install_commands(README.read_text(encoding="utf-8")), (
        "no install command found in a code block; this test would pass vacuously"
    )


def test_no_install_line_resolves_one_of_ours_against_pypi():
    for args in install_commands(README.read_text(encoding="utf-8")):
        assert not problems(args), "README install `%s` cannot run as written: %s" % (args, problems(args))


GIT_URL = "git+https://github.com/dhyabi2/gpt-researcher-x402-retriever"


def test_the_paying_section_installs_the_feeless402_extra():
    commands = section_install_commands(README.read_text(encoding="utf-8"), PAYING_HEADING)
    assert commands, "no install command under the %r heading" % PAYING_HEADING
    assert any(carries_extra(args, PAYING_EXTRA) for args in commands), commands


@pytest.mark.parametrize("markdown", [
    "### %s\n\n```bash\npip install \"gpt-researcher-x402-retriever @ %s\"\n```\n" % (PAYING_HEADING, GIT_URL),
    "### %s\n\n```bash\npip install \"feeless402 @ %s\"\n```\n" % (PAYING_HEADING, GIT_URL),
])
def test_the_paying_check_refuses_a_line_without_the_extra(markdown):
    commands = section_install_commands(markdown, PAYING_HEADING)
    assert commands and not any(carries_extra(args, PAYING_EXTRA) for args in commands)


def test_the_extras_fallback_agrees_with_tomllib():
    tomllib = pytest.importorskip("tomllib")
    text = PYPROJECT.read_text(encoding="utf-8")
    expected = {canonical(k) for k in tomllib.loads(text)["project"]["optional-dependencies"]}
    assert _declared_extras_fallback(text) == expected == declared_extras()
    assert "feeless402" in expected


def test_the_readme_line_17_form_refuses_a_typo_in_the_repo_name():
    assert not problems('"git+https://github.com/dhyabi2/gpt-researcher-x402-retriever"')
    assert problems('"git+https://github.com/dhyabi2/gpt-researcher-x402-retreiver"')
    assert problems('"git+https://github.com/dhyabi2/gpt-researcher-x402"')


def test_the_not_on_pypi_names_are_still_mentioned():
    """Drop a name from NOT_ON_PYPI only once the README stops mentioning it."""
    text = README.read_text(encoding="utf-8")
    for name in NOT_ON_PYPI:
        assert name in text


GIT = GIT_URL

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
    # a bare URL is judged too: wrong owner, an #egg= of ours elsewhere, a path walking out
    "```\npip install git+https://github.com/someone/gpt-researcher-x402-retriever\n```",
    '```\npip install -e "git+https://github.com/someone/x#egg=gpt-researcher-x402-retriever"\n```',
    "```\npip install git+https://github.com/dhyabi2/gpt-researcher-x402-retriever/../../someone/evil\n```",
    "```\npip install git+https://github.com/dhyabi2/gpt-researcher-x402-retreiver\n```",
    # the right path on a wrong host or scheme
    "```\npip install git+https://gitub.com/dhyabi2/gpt-researcher-x402-retriever\n```",
    "```\npip install git+https://gitlab.com/dhyabi2/gpt-researcher-x402-retriever\n```",
    "```\npip install git+htps://github.com/dhyabi2/gpt-researcher-x402-retriever\n```",
    "```\npip install https://github.com/dhyabi2/gpt-researcher-x402-retriever/archive/main.zip/../../x.zip\n```",
    # extras must exist in pyproject.toml
    '```\npip install "gpt-researcher-x402-retriever[feeles402] @ %s"\n```' % GIT,
    '```\npip install -e ".[tset]"\n```',
    "```\npip install -e %s#egg=gpt-researcher-x402-retriever[nope]\n```" % GIT,
]

GOOD = [
    '```bash\npip install "gpt-researcher-x402-retriever[feeless402] @ %s"\n```' % GIT,
    '```\npip install "%s"\n```' % GIT,
    "```\npip install gpt-researcher-x402-retriever @ %s@main\n```" % GIT,
    '```\npip install "gpt-researcher-x402-retriever @ '
    'https://github.com/dhyabi2/gpt-researcher-x402-retriever/archive/refs/heads/main.zip"\n```',
    '```\npip install -e ".[test]"\n```',
    "```\npip install git+ssh://git@github.com/dhyabi2/gpt-researcher-x402-retriever.git@main\n```",
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
