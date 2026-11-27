"""Partner-facing UI: brief, quiet relationships, and the confidentiality evidence (egress audit)."""
from __future__ import annotations

import json
import os

import httpx
import streamlit as st

API = os.getenv("API_URL", "http://localhost:8000")
st.set_page_config(page_title="Hatton Relationship Intelligence", layout="wide")


def api(method, path, **kw):
    try:
        r = httpx.request(method, f"{API}{path}", timeout=60, **kw)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return None


SECTION_TITLES = {"who": "Who they are", "history": "Our history", "last_discussed": "Last discussed",
                  "our_commitments": "What we said we'd do", "their_commitments": "What they said they'd do",
                  "signals": "Signals"}

partners = api("GET", "/partners") or []
pmap = {p["name"]: p["email"] for p in partners}
st.sidebar.title("Hatton RI")
me = st.sidebar.selectbox("Partner", ["(any)"] + list(pmap))
partner_email = pmap.get(me)
stats = api("GET", "/stats") or {}
st.sidebar.caption(f"As of {stats.get('as_of', '?')[:16]}")
st.sidebar.metric("Messages in graph", f"{stats.get('messages', 0):,}")
st.sidebar.metric("Relationships", f"{stats.get('edges', 0):,}")
st.sidebar.caption("Read-only system: it never emails, schedules or contacts anyone.")

review_queue = api("GET", "/review", params={"limit": 50}) or []
n_review = f"{len(review_queue)}+" if len(review_queue) == 50 else str(len(review_queue))
tab_brief, tab_review, tab_quiet, tab_egress, tab_graph = st.tabs(
    ["Pre-meeting brief", f"Needs partner review ({n_review})", "Gone quiet", "What leaves the region", "Graph"])


def render(res):
    if not res:
        return
    mode = res.get("mode")
    for w in res.get("warnings", []):
        st.warning(w)
    if mode == "ambiguous":
        st.info("Several contacts share this name. Pick one:")
        for c in res.get("candidates") or []:
            if st.button(f"{c['name']} - {c.get('title')}, {c.get('org')} ({c['email']})", key=f"c{c['person_id']}"):
                st.session_state["brief"] = api("POST", "/brief", json={"person_id": c["person_id"],
                                                                         "partner_email": partner_email})
                st.rerun()
        return
    b = res.get("brief")
    if not b:
        st.error(f"No brief ({mode}).")
        return
    p, r = b["person"], b.get("relationship") or {}
    badge = {"llm": "verified", "cached": "prepared earlier", "fallback": "FACTS ONLY (model down)",
             "stale_cache": "STALE (data service down)"}.get(mode, mode)
    st.subheader(f"{p['name']} · {p.get('title') or ''} · {p.get('org') or ''}")
    rv = res.get("review") or b.get("review") or {}
    if rv.get("needed"):
        st.error("**Flagged for partner review:** " + " ".join(f"({i}) {x}" for i, x in enumerate(rv["reasons"], 1))
                 + (f"  \nBrief #{res['brief_id']} is in the *Needs partner review* tab." if res.get("brief_id") else ""))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status", r.get("status", "-"))
    c2.metric("Warmth", r.get("warmth", "-"), help=f"peak {r.get('peak')}")
    c3.metric("Last time they engaged", r.get("last_time_they_engaged", "-"))
    c4.metric("Brief", badge, help=f"{res.get('latency_ms')} ms · ${res.get('cost_usd')}")
    for key, title in SECTION_TITLES.items():
        items = b["sections"].get(key) or []
        if items:
            st.markdown(f"**{title}**")
            for it in items:
                st.markdown(f"- {it['text']}  <span style='color:gray;font-size:0.8em'>{' '.join(it['cites'])}</span>",
                            unsafe_allow_html=True)
    if b.get("dropped_claims"):
        with st.expander(f"{len(b['dropped_claims'])} claim(s) removed by the verifier"):
            for d in b["dropped_claims"]:
                st.markdown(f"- ~~{d['text']}~~ - {d['reason']}")
    with st.expander("Run details"):
        st.json({k: res.get(k) for k in ("mode", "latency_ms", "cost_usd", "timings", "run_id", "trace_id")}
                | {"context": b.get("context_stats")})


with tab_brief:
    up = api("GET", "/meetings/upcoming", params={"partner": partner_email, "days": 1}) or []
    col1, col2 = st.columns([2, 1])
    with col1:
        q = st.text_input("Who are you meeting?", placeholder="Name or email")
        if st.button("Brief me", type="primary") and q:
            with st.spinner("Preparing brief..."):
                st.session_state["brief"] = api("POST", "/brief", json={"query": q, "partner_email": partner_email})
    with col2:
        st.markdown("**Next 24h**")
        for m in up[:12]:
            if st.button(f"{m['start_at'][11:16]} {m['title']}", key=m["id"] + m["partner_email"]):
                with st.spinner("Preparing brief..."):
                    st.session_state["brief"] = api("POST", "/brief", json={"meeting_id": m["id"],
                                                                             "partner_email": m["partner_email"]})
    render(st.session_state.get("brief"))

with tab_review:
    st.caption("Briefs the system was not sure about. Check them, then approve or reject with a note. "
               "Decisions are recorded in Hatton's own database only.")
    if not review_queue:
        st.success("Nothing waiting for review.")
    for item in review_queue:
        bid = item["brief_id"]
        with st.container(border=True):
            st.markdown(f"**#{bid} · {item['person']}** · {item['mode']} · {str(item['created_at'])[:16]}"
                        + (f" · meeting {item['meeting_id']}" if item.get("meeting_id") else ""))
            for reason in item.get("reasons") or []:
                st.markdown(f"- {reason}")
            note = st.text_input("Note", key=f"note{bid}", placeholder="What did you check?")
            a, r_ = st.columns(2)
            for col, decision, label in ((a, "approve", "Approve"), (r_, "reject", "Reject")):
                if col.button(label, key=f"{decision}{bid}"):
                    if api("POST", f"/review/{bid}", json={"decision": decision, "note": note or None,
                                                          "reviewer": me if partner_email else None}):
                        st.rerun()

with tab_quiet:
    rows = api("GET", "/quiet", params={"partner": partner_email}) or []
    st.caption("Relationships that were warm and have gone quiet relative to their own usual rhythm.")
    st.dataframe([{"Partner": r["partner"], "Contact": r["name"], "Org": r["org"], "Kind": r["kind"],
                   "Peak warmth": r["warmth_peak"], "Now": r["warmth"],
                   "Silent (days)": r["explanation"]["days_since_two_way_contact"],
                   "Usual gap (days)": r["explanation"]["median_gap_days"],
                   "Unanswered since": r["explanation"]["unanswered_outbound_since"]} for r in rows],
                 use_container_width=True)

with tab_egress:
    st.caption("Every request that left the region, exactly as sent. Names, emails, phones, addresses, "
               "organisations and deal codenames are replaced by pseudonyms before this point; the mapping "
               "back stays in our database.")
    rows = api("GET", "/egress", params={"limit": 30}) or []
    st.metric("Blocked egress attempts", stats.get("egress_blocked", 0))
    for r in rows:
        with st.expander(f"#{r['id']} {r['at'][:19]} {r['purpose']} -> {r['host']} "
                         f"{'ALLOWED' if r['allowed'] else 'BLOCKED'} {r['status'] or ''}"):
            try:
                body = json.loads(r["request_body"] or "{}")
                st.markdown("**System prompt**")
                st.code(body.get("system", "")[:3000])
                st.markdown("**User content sent**")
                st.code(body["messages"][0]["content"][:12000] if body.get("messages") else "")
            except Exception:
                st.code((r["request_body"] or "")[:12000])

with tab_graph:
    st.json(stats)
