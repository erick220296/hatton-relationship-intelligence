"""The single door out of the region.

Every outbound HTTP request made by our code goes through `egress_client()`:
  * the host must be on ALLOWED_HOSTS, otherwise EgressBlocked is raised
    before any bytes are sent;
  * the exact request body and the response body are written to `egress_audit`.

Combined with masking (hri.privacy.masker), the egress_audit table is the
evidence for "show exactly what leaves the region and what does not".
"""
from __future__ import annotations

import contextvars
import logging

import anthropic

from hri import db

log = logging.getLogger(__name__)

ALLOWED_HOSTS = {"api.anthropic.com"}
purpose_var: contextvars.ContextVar[str] = contextvars.ContextVar("egress_purpose", default="unspecified")


class EgressBlocked(RuntimeError):
    pass


def _audit(**row):
    try:
        db.q("""INSERT INTO egress_audit (host, method, path, purpose, allowed, request_body, status, response_body)
                VALUES (%(host)s, %(method)s, %(path)s, %(purpose)s, %(allowed)s, %(request_body)s,
                        %(status)s, %(response_body)s)""",
             {"status": None, "response_body": None, "request_body": None, **row})
    except Exception as e:  # auditing must never be silently skipped: surface loudly
        log.error("egress audit write failed: %s", e)
        raise


def _on_request(request):
    host = request.url.host
    body = request.content.decode("utf-8", "replace") if request.content else None
    if host not in ALLOWED_HOSTS:
        _audit(host=host, method=request.method, path=request.url.path, purpose=purpose_var.get(),
               allowed=False, request_body=body)
        raise EgressBlocked(f"egress to {host} is not allowlisted")
    request.extensions["hri_body"] = body


def _on_response(response):
    response.read()
    req = response.request
    _audit(host=req.url.host, method=req.method, path=req.url.path, purpose=purpose_var.get(), allowed=True,
           request_body=req.extensions.get("hri_body"), status=response.status_code,
           response_body=response.text[:200_000])


def egress_client(timeout: float = 60.0):
    """The SDK's own (httpx2-based) client, with our allowlist + audit hooks attached."""
    return anthropic.DefaultHttpxClient(timeout=timeout,
                                        event_hooks={"request": [_on_request], "response": [_on_response]})
