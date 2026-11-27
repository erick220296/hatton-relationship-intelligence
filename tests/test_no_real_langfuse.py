"""B4: during tests Langfuse keys are blank, so stub-model runs never reach the real dashboard."""
from __future__ import annotations

import os

import pytest

from conftest import LOCAL_LANGFUSE


def test_langfuse_keys_are_blank_and_tracing_is_off():
    if LOCAL_LANGFUSE.match(os.getenv("LANGFUSE_HOST", "")):
        pytest.skip("explicit local-sink run (LANGFUSE_HOST is localhost)")
    from hri.obs import tracing
    assert os.environ.get("LANGFUSE_PUBLIC_KEY") == "" and os.environ.get("LANGFUSE_SECRET_KEY") == ""
    assert tracing._enabled is False
    assert tracing.get_client() is None
