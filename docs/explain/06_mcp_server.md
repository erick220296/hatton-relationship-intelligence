# 06 · The MCP server (read-only data tools)

## In one sentence
The MCP server is a separate small service that answers questions about the relationship graph, such as "find this person", "recent threads" and "commitments". It can only read: it logs into the database with a role that is allowed to SELECT and nothing else, and it cannot see the pseudonym vault.

## How it works

### Terms
- **MCP (Model Context Protocol):** an open standard for connecting AI applications to tools and data. A tool server lists its tools, and a client calls them over HTTP.
- **Tool:** a named function with typed inputs, e.g. `get_recent_threads(person_id)`.
- **Database role:** a database login with specific permissions.

### The server (`src/hri/mcp_server/server.py`)
- Built with *FastMCP*. It runs in its own container on port 8001 and talks over HTTP (`stateless_http=True`).
- It connects to Postgres as `hri_ro` (`docker-compose.yml`: `DATABASE_URL: postgresql://hri_ro:...`).
- Every tool carries the label `readOnlyHint=True, destructiveHint=False` (`RO` annotations).

### The 9 tools
| Tool | What it returns |
|---|---|
| `find_person(query, partner_email?)` | up to 10 people matching a name or email, with which partners know them and how warmly |
| `get_relationship(person_id)` | profile + every partner's warmth, status, counts and dates + up to 8 colleagues |
| `get_recent_threads(person_id, limit_threads=8)` | the newest email threads with full text and message IDs, only up to the system's "now" |
| `get_thread_index(person_id)` | one line per older thread (date, subject, count, first message ID) |
| `get_commitments(person_id)` | extracted commitments with their source message IDs |
| `get_meeting(meeting_id)` | a calendar event and its attendees |
| `upcoming_meetings(partner_email?, days=1)` | meetings in the next N days |
| `search_messages(person_id, query, k)` | similarity search over that person's emails (local embedding of the query) |
| `quiet_relationships(partner_email?)` | the gone-quiet list |

### The client side (`src/hri/agent/tools.py`)
- `mcp_tools()` opens one short session per step, with 5 s to connect.
- `MCPTools.call()` gives each call 8 s. Any failure (timeout, connection drop, server error) becomes `ToolUnavailable`, which the brief agent turns into mode `unavailable` → the stale-cache fallback (file 10).

### The read-only guarantee: three layers
1. **Database.** `schema.sql` creates `hri_ro` with SELECT only, then revokes even that on the vault and the egress log. Test: `test_no_outbound.py: test_tool_database_role_cannot_write` (INSERT, UPDATE, DELETE and vault SELECT all refused). Ash confirmed it by hand on 27 Nov: "permission denied".
2. **Tool surface.** `test_mcp_tools_are_all_read_only` checks that every tool is labelled read-only and that no tool name starts with an action verb (send, create, schedule, delete …).
3. **No action code anywhere.** Other tests check there are no mail or calendar libraries and no send-like calls (file 09).

### Why MCP if the model does not choose tools?
In this build, **code** decides which tools to call. The `gather` step always calls the same four. The model never sees a tool list. So MCP is not used for "the AI picks tools". It is used as a **clean, swappable, provably read-only data boundary** (`ADR.md D7`):
- **Separation.** The agent never writes SQL. Swapping Outlook for Gmail changes ingest and storage, not the agent.
- **Least privilege.** The only process the agent talks to for data physically cannot write, because of its database role.
- **A real failure point to demo.** It is a separate container, so `make kill-mcp` really kills a tool mid-session, and the system falls back to the last stored brief.
- **Reuse.** The same tools could be offered to Claude Desktop or another agent later without rewriting anything.

## Why it was built this way
- The client rule "no outbound action, ever" is easiest to prove when the data layer cannot even write.
- A standard protocol (MCP) instead of ad-hoc functions makes the boundary visible and testable, and the course expects tool use via MCP.
- **Rejected:**
  - Letting the agent query the database directly: it could then reach the vault, with no enforced boundary.
  - A free tool-calling agent that picks tools itself: slower, less predictable, a bigger injection surface (`ADR.md D6`).

## What breaks it
- **No authentication.** The tool server has no login. Anyone who can reach port 8001 can read unmasked email through the tools. The same goes for Postgres on 5432 with default passwords (`ADR.md §16`).
  - In the committed code both ports are published to the network.
  - An uncommitted change in the working tree (`docker-compose.yml`, 27 Nov evening) binds 5432 and 8001 to `127.0.0.1`, i.e. this machine only. Check whether it was committed before the panel.
- **The read-only role still sees raw data.** It can read email bodies, commitments and stored briefs. Only the vault and the egress log are hidden. That is by design (the data is in-region), but it is not "masked".
- **Possible concurrency limit.** The tools are ordinary (synchronous) functions. I did not verify whether FastMCP runs them one at a time; if it does, a burst of briefs queues up behind slow queries and can hit the 8 s client timeout. No load test with real data tools under load has been reported.
- **Cross-partner visibility.** Threads and commitments come from all six partners' mailboxes. One partner's brief can show another partner's correspondence (`get_recent_threads`, `get_commitments`). A product decision is needed.
- **A misleading docstring.** `upcoming_meetings` says it returns "confirmed" meetings but does not filter. `/precompute` filters instead.
- **Odd search behaviour.** A blank or very short search matches everyone (`find_person` uses `ILIKE %q%`). The agent then says "ambiguous", which is safe but odd.

## Agentic pattern / course concept it shows
*Tool use via MCP with least privilege*, and a *separation of concerns* between reasoning and data access. It also gives a *real failure injection point* for the resilience demo.

## 3 likely panel questions
1. **"How do you guarantee the agent can't write or send anything?"**
   Three layers. The tool server logs in with a SELECT-only role that cannot even read the vault, and a test tries INSERT, UPDATE and DELETE and expects "permission denied". Every tool is labelled read-only and no tool has an action name. There is no mail or calendar library in the code. All three are automated tests.
2. **"Why MCP if your code, not the model, picks the tools?"**
   We use MCP as the data boundary, not for model tool-choice. It keeps SQL out of the agent, makes the boundary provably read-only, lets us kill it for the failure demo, and lets other AI clients reuse the same tools later. A fixed workflow is our deliberate choice for speed and safety.
3. **"What happens when the tool server dies?"**
   Each tool call has an 8-second timeout. On failure the agent stops with mode `unavailable`. The API then serves the last stored brief for that person, labelled stale, with its age (fix F09). `make kill-mcp` shows it.
