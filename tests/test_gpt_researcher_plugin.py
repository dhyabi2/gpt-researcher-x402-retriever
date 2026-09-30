"""GPT Researcher finds this retriever by name through its entry-point group.

Loads GPT Researcher's own ``actions/retriever.py`` - from the installed package,
or by path from a checkout named in ``GPTR_RETRIEVER_PY`` - and asks it for
``paypercall``. Skipped when neither is available.
"""
import importlib.util
import os
import types

import pytest

import gpt_researcher_x402_retriever.retriever as retriever_mod
from gpt_researcher_x402_retriever import PayPerCallSearch


def _gptr_retriever_module():
    path = os.environ.get("GPTR_RETRIEVER_PY")
    if path:
        spec = importlib.util.spec_from_file_location("gptr_actions_retriever", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    try:
        from gpt_researcher.actions import retriever
        return retriever
    except Exception:
        pytest.skip("GPT Researcher not installed and GPTR_RETRIEVER_PY not set")


def test_entry_point_registers_paypercall():
    from importlib.metadata import entry_points
    eps = entry_points(group="gpt_researcher.retrievers", name="paypercall")
    assert [ep.load() for ep in eps] == [PayPerCallSearch]


def test_gpt_researcher_resolves_retriever_paypercall():
    module = _gptr_retriever_module()
    assert module.get_retriever("paypercall") is PayPerCallSearch


class _Tavily:
    """Stands in for GPT Researcher's default retriever, which needs TAVILY_API_KEY."""


def _upstream_stand_in():
    """A module shaped like gpt_researcher.actions.retriever, copied from 0.15.1.

    The two lines that matter are verbatim: `get_retriever` ends in `case _: return None`
    (a hardcoded match over the built-in names; no entry points are read), and
    `get_retrievers` does `get_retriever(r) or get_default_retriever()`.
    """
    module = types.ModuleType("gptr_actions_retriever_stand_in")

    def get_retriever(retriever):
        return {"duckduckgo": object, "tavily": _Tavily}.get(retriever)  # case _: return None

    def get_default_retriever():
        return _Tavily

    def get_retrievers(headers, cfg):
        names = [r.strip() for r in cfg.retrievers.split(",")]
        return [module.get_retriever(r) or module.get_default_retriever() for r in names]

    module.get_retriever = get_retriever
    module.get_default_retriever = get_default_retriever
    module.get_retrievers = get_retrievers
    return module


class _Cfg:
    def __init__(self, retrievers):
        self.retrievers = retrievers
        self.retriever = None


def test_without_register_the_name_falls_back_to_the_default_retriever():
    """The defect: RETRIEVER=paypercall silently becomes Tavily, which needs an API key."""
    module = _upstream_stand_in()
    assert module.get_retriever("paypercall") is None
    assert module.get_retrievers({}, _Cfg("paypercall")) == [_Tavily]


def test_register_makes_the_name_resolve():
    module = _upstream_stand_in()
    assert retriever_mod._install(module) is True
    assert module.get_retriever("paypercall") is PayPerCallSearch
    assert module.get_retrievers({}, _Cfg("paypercall")) == [PayPerCallSearch]


def test_register_leaves_other_names_alone_and_is_idempotent():
    module = _upstream_stand_in()
    retriever_mod._install(module)
    retriever_mod._install(module)
    assert module.get_retriever("tavily") is _Tavily
    assert module.get_retriever("nonsense") is None
    assert module.get_retrievers({}, _Cfg("paypercall,duckduckgo")) == [PayPerCallSearch, object]


def test_register_reports_failure_rather_than_raising():
    assert retriever_mod._install(types.ModuleType("empty")) is False


def test_register_against_the_real_gpt_researcher():
    """Same two assertions against GPT Researcher's own module, when one is available."""
    module = _gptr_retriever_module()
    if getattr(module.get_retriever, "_paypercall_registered", False):
        pytest.skip("already registered in this process")
    assert module.get_retriever("paypercall") is None
    assert retriever_mod._install(module) is True
    assert module.get_retriever("paypercall") is PayPerCallSearch
