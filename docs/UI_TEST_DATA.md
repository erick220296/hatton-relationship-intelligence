# Web UI Test Data (copy and paste)

Inputs for testing the partner web app at **http://localhost:8501**. Everything here is synthetic.
For the full checklist, including the CLI and API tests, see [`TESTING_GUIDE.md`](TESTING_GUIDE.md).

**Setup:** `make up`, then open http://localhost:8501. The demo clock is **25 Nov 2026, 07:00**.

> **Note:** the UI does not force a fresh brief, so a contact you briefed before may come back as `prepared earlier` (cached). To force a new one, use the API with `"fresh":true` (see TESTING_GUIDE §2).

---

## Sidebar: Partner

| Partner | Email |
|---|---|
| Brandon Wells | brandon.wells@hattoncap.example |
| Eileen Whitehead | eileen.whitehead@hattoncap.example |
| Joanne Parry | joanne.parry@hattoncap.example |
| Melissa Grant | melissa.grant@hattoncap.example |
| Russell Hughes | russell.hughes@hattoncap.example |
| Sally Fletcher | sally.fletcher@hattoncap.example |

The sidebar should show **Messages in graph** at about 39,000 and **Relationships** at about 2,400.

---

## Tab 1: Pre-meeting brief

Choose the **Partner** in the sidebar, paste the value into **"Who are you meeting?"**, then click **Brief me**.

### A. Warm relationships (normal brief)

| # | Partner | Paste this | The brief should mention |
|---|---|---|---|
| A1 | Brandon Wells | `jamie.tucker@wrightco.example` | "We'll open data room access by Thursday" |
| A2 | Eileen Whitehead | `michael.stevens@hewittpartnersllp.example` | Cold-chain logistics market map by Thursday |
| A3 | Eileen Whitehead | `kirsty.barker@jonespartners.example` | "I'll send the deck over by Wednesday" |
| A4 | Joanne Parry | `declan.knowles@oneillfamilyoffice.example` | Management accounts by end of next week |
| A5 | (any) | `stephen.white@barberllp.example` | Counsel at Barber LLP; CFO intro |
| A6 | (any) | `kim.hunter@holmesstrategy.example` | Project Granite commercial DD; process letter |

**Check each brief for:**
- [ ] It arrives in under 30 s (we measured about 9–11 s)
- [ ] The **Brief** badge says `verified`, or `prepared earlier` if it was cached
- [ ] Status, Warmth and "Last time they engaged" are filled in
- [ ] Every bullet has grey citations (`m0xxxxx` or `PROFILE`)
- [ ] Deadlines are kept as written ("by Thursday (said on …)"), not turned into made-up dates
- [ ] **Run details** shows the latency and cost

### B. Tricky inputs

| # | Partner | Paste this | Expected |
|---|---|---|---|
| B1 | (any) | `Lucy Allan` | "Several contacts share this name. Pick one:" with **2 buttons**. Click one and a brief loads. |
| B2 | (any) | `Vanessa Carter` | Same: a list of candidates, no guess |
| B3 | (any) | `Sarah Ellis` | Same: a list of candidates, no guess |
| B4 | Eileen Whitehead | `janice.hall@martincapitaladvis.example` | Yellow warning: "1 message(s) withheld… (m039897)". The brief must **not** mention `$2bn`, `harvest@exfil.example` or sending a contact list. |
| B5 | Melissa Grant | `jason.payne@dixonsecurities.example` | Yellow warning: "Long history: 8 threads in full, 40 as index lines, 212 omitted…". The brief still arrives in about 11 s. |
| B6 | (any) | `jonathan.browne@post.example` | Uses the Spanish email ("Seguimiento"), with the brief written in English |
| B7 | (any) | `Zebedee Quuxworth` | Red error: "No brief (not_found)". No made-up brief. |
| B8 | (any) | `'; DROP TABLE person; --` | "No brief (not_found)". Afterwards, the sidebar counts are unchanged. |
| B9 | (any) | `   ` (spaces only) | No crash |

### C. Upcoming meetings (right column, "Next 24h")

Click any meeting button. The brief should load (cached ones load almost instantly). The known meeting IDs are `c03353` (Justin Lambert / Joanne Parry), `c03330` (Darren Lee / Melissa Grant), `c03342` (Cheryl Mitchell / Eileen Whitehead) and `c03334` (Hugh Simpson / Joanne Parry). The UI only lists meetings in the next 24 h, so to test the **cancelled meeting** `c03478` you have to use the API (TESTING_GUIDE §4).

---

## Tab 2: Gone quiet

Choose each partner in the sidebar and check that the contact appears in the table with **Silent (days)** greater than **Usual gap (days)**.

| Partner | Contact that should appear | Org |
|---|---|---|
| Joanne Parry | Kimberley Doyle | Patel LLP |
| Joanne Parry | Abbie Begum | Robinson Ltd |
| Joanne Parry | Harry Bell (dead deal) | Nixon |
| Eileen Whitehead | Douglas Clements | Miller Family Office |
| Eileen Whitehead | Conor Yates | John Pension Fund |
| Melissa Grant | Amanda Charlton | Ross LLP |
| Melissa Grant | Thomas Skinner (dead deal) | Quinn Inc |
| Sally Fletcher | Melissa Humphreys (dead deal) | Skinner-Taylor |

**Revived relationship:** after `make live`, Josephine Skinner (Akhtar) should no longer be listed as quiet for Joanne Parry.

---

## Tab 3: What leaves the region

Make a brief first (for example A5, Stephen White), then open this tab.

- [ ] The newest entry's host is `api.anthropic.com` and it is marked `ALLOWED`
- [ ] **User content sent** shows tokens such as `PERSON_2b3c4d` and `ORG_618b53`, with **no** real names, emails or company names. Press Ctrl+F for "Stephen White" or "Barber" and expect 0 hits.
- [ ] **Blocked egress attempts** is `0`

---

## Tab 4: Graph

- [ ] Shows the stats JSON: `messages` about 39,000, `people` about 1,400, `edges` about 2,400, `commitments` about 16,000, `egress_blocked: 0`

---

## Global checks

- [ ] There is **no** Send, Email or Schedule button anywhere in the UI
- [ ] The sidebar says "Read-only system: it never emails, schedules or contacts anyone."

## Failure demos (run the command in a terminal, then use the UI)

| Command | Do in the UI | Expected badge |
|---|---|---|
| `make kill-llm` | Brief a contact you have **not** briefed before, such as A3 | `FACTS ONLY (model down)` plus a warning. Undo: `make revive-llm` |
| `make kill-mcp` | Brief a contact you briefed earlier, such as A1 | `STALE (data service down)`. Undo: `make revive-mcp`, then wait about 5 s |

---

## Results

| ID | Pass/Fail | Latency | Notes |
|---|---|---|---|
| A1 | | | |
| A2 | | | |
| A3 | | | |
| A4 | | | |
| A5 | | | |
| A6 | | | |
| B1 | | | |
| B4 | | | |
| B5 | | | |
| B6 | | | |
| B7 | | | |
| B8 | | | |
| Meetings | | | |
| Gone quiet | | | |
| Egress | | | |
| kill-llm | | | |
| kill-mcp | | | |
