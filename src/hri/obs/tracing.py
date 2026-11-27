"""Langfuse instrumentation.

Langfuse is a third party, so it only ever receives MASKED text: generation
inputs/outputs are the exact masked strings sent to the model, and span
inputs are IDs and counts, never raw email content.

If Langfuse keys are absent the decorators are no-ops, so the system still runs.
"""
from __future__ import annotations

import contextlib
import functools
import logging
import os

log = logging.getLogger(__name__)

_enabled = bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))

if _enabled:
    from langfuse import get_client, observe, propagate_attributes  # type: ignore
else:  # pragma: no cover - fallback when Langfuse is not configured
    def observe(*dargs, **dkwargs):
        if dargs and callable(dargs[0]):
            return dargs[0]

        def deco(fn):
            @functools.wraps(fn)
            def wrapper(*a, **k):
                return fn(*a, **k)
            return wrapper
        return deco

    def get_client():
        return None

    def propagate_attributes(**kw):
        return contextlib.nullcontext()


def record_generation(*, name, model, input_text, output_text, tokens_in, tokens_out, cost):
    lf = get_client()
    if not lf:
        return
    try:
        with lf.start_as_current_observation(as_type="generation", name=name, model=model, input=input_text) as gen:
            gen.update(output=output_text, usage_details={"input": tokens_in, "output": tokens_out},
                       cost_details={"total": cost})
    except Exception as e:  # observability must never break a brief
        log.warning("langfuse generation record failed: %s", e)


def span_meta(**kw):
    """Attach ids/counts/timings to the current span. Callers must pass no text from mail or briefs."""
    lf = get_client()
    if not lf:
        return
    try:
        lf.update_current_span(metadata=kw)
    except Exception as e:  # observability must never break a brief
        log.warning("langfuse span update failed: %s", e)


def trace_attributes(*, tags: list[str] | None = None, session_id: str | None = None):
    """Trace-level tags/session for the current trace and every span opened inside the block.
    Only fixed labels and ids: never names or mail text."""
    try:
        return propagate_attributes(tags=tags, session_id=session_id)
    except Exception as e:  # observability must never break a brief
        log.warning("langfuse trace attributes failed: %s", e)
        return contextlib.nullcontext()


def score_trace(**scores):
    """Numeric scores on the current trace (e.g. latency_ms, dropped_ratio). None values are skipped."""
    lf = get_client()
    if not lf:
        return
    for name, value in scores.items():
        if value is None:
            continue
        try:
            lf.score_current_trace(name=name, value=float(value), data_type="NUMERIC")
        except Exception as e:
            log.warning("langfuse score %s failed: %s", name, e)


def update_trace(**kw):
    lf = get_client()
    if lf:
        try:
            lf.update_current_trace(**kw)
        except Exception as e:
            log.warning("langfuse trace update failed: %s", e)


def current_trace_id() -> str | None:
    lf = get_client()
    if not lf:
        return None
    try:
        return lf.get_current_trace_id()
    except Exception:
        return None


def flush():
    lf = get_client()
    if lf:
        lf.flush()
