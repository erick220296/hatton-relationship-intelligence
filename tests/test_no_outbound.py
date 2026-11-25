"""Definition of Done #5: the system takes no outbound action of any kind, proven by test.

Three independent layers:
  1. Static: no mail/calendar-write/messaging libraries or calls anywhere in src/.
  2. Tool surface: every MCP tool is read-only; none has a send/schedule/write verb.
  3. Network: the only HTTP client that can leave the region refuses non-allowlisted hosts,
     and the database role used by the tools cannot write.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "hri"

FORBIDDEN_IMPORTS = {"smtplib", "imaplib", "poplib", "email.mime", "sendgrid", "twilio", "slack_sdk",
                     "googleapiclient", "msgraph", "O365", "exchangelib", "requests", "urllib.request", "aiosmtplib"}
FORBIDDEN_CALL_RE = re.compile(r"\b(send_?mail|sendmail|send_message|create_event|events\(\)\.insert|"
                               r"schedule_meeting|post_message|chat_postMessage)\b", re.I)
WRITE_VERBS = re.compile(r"^(send|email|mail|post|create|schedule|book|invite|reply|forward|delete|update|"
                         r"write|insert|notify|contact|message)", re.I)


def _py_files():
    return [p for p in SRC.rglob("*.py")]


def test_no_outbound_libraries_imported():
    offenders = []
    for path in _py_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                if any(n == f or n.startswith(f + ".") for f in FORBIDDEN_IMPORTS):
                    offenders.append(f"{path.relative_to(SRC)}: import {n}")
    assert not offenders, offenders


def test_no_outbound_calls_in_source():
    offenders = [f"{p.relative_to(SRC)}" for p in _py_files() if FORBIDDEN_CALL_RE.search(p.read_text())]
    assert not offenders, offenders


def test_only_egress_module_builds_http_clients():
    """httpx clients may only be created in privacy/egress.py (plus the UI talking to our own API)."""
    allowed = {"privacy/egress.py", "ui/streamlit_app.py"}
    offenders = []
    for p in _py_files():
        rel = str(p.relative_to(SRC))
        if rel in allowed:
            continue
        if re.search(r"(httpx2?\.(Client|AsyncClient|get|post|request)|DefaultHttpxClient|DefaultAsyncHttpxClient)\(", p.read_text()):
            offenders.append(rel)
    assert not offenders, offenders


def test_mcp_tools_are_all_read_only():
    from hri.mcp_server.server import mcp
    tools = mcp._tool_manager.list_tools()
    assert tools, "no tools registered"
    for t in tools:
        assert t.annotations and t.annotations.readOnlyHint is True, f"{t.name} not marked read-only"
        assert t.annotations.destructiveHint is False, t.name
        assert not WRITE_VERBS.match(t.name), f"tool name implies an action: {t.name}"


def test_api_has_no_action_endpoints():
    from hri.api.app import app
    action = re.compile(r"(send|email|mail|schedule|book|invite|contact|notify)", re.I)
    for r in app.routes:
        path = getattr(r, "path", "")
        assert not action.search(path), f"action-like endpoint: {path}"


def test_egress_blocks_non_allowlisted_hosts(monkeypatch):
    from hri.privacy import egress
    audited = []
    monkeypatch.setattr(egress, "_audit", lambda **row: audited.append(row))
    with egress.egress_client() as c:
        with pytest.raises(egress.EgressBlocked):
            c.post("https://smtp.gmail.com/send", content=b"hello")
        with pytest.raises(egress.EgressBlocked):
            c.get("https://graph.microsoft.com/v1.0/me/sendMail")
    assert [a["allowed"] for a in audited] == [False, False]
    assert audited[0]["host"] == "smtp.gmail.com"


@pytest.mark.db
def test_tool_database_role_cannot_write():
    import psycopg
    with psycopg.connect("postgresql://hri_ro:hri_ro@db:5432/hri", autocommit=True) as c:
        for sql in ["INSERT INTO kv (key, value) VALUES ('x', '1')", "UPDATE person SET name = 'x'",
                    "DELETE FROM message", "SELECT * FROM pseudonym LIMIT 1"]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql)
