"""GPT Researcher finds this retriever by name through its entry-point group.

Loads GPT Researcher's own ``actions/retriever.py`` - from the installed package,
or by path from a checkout named in ``GPTR_RETRIEVER_PY`` - and asks it for
``paypercall``. Skipped when neither is available.
"""
import importlib.util
import os

import pytest

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
