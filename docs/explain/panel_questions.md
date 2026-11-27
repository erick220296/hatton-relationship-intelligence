# Panel questions: the whole system (drill sheet)

Organised by the marking scheme. Each answer is short enough to say in about 20 seconds. Bold = the phrase to land. Numbers come from `docs/cost_model.md`, `docs/evidence/`, `work/eval_batch1.md` and `docs/ADR.md`.

## A. Agentic architecture and design patterns (20 marks)
1. **"Is this even an agent? It looks like a script."** It's an agentic **workflow**, deliberately. Anthropic's "Building effective agents" says to use workflows with fixed paths when the task is a known procedure, and agents when the steps can't be predicted. A brief is a known procedure. The model does the language work (writing, verifying, extracting promises); code decides the route. **We chose predictability because the client's rules are about accuracy and 30 seconds.**
2. **"Why not multi-agent? Your proposal had five agents."** One shared context, a 30-second limit, and **multi-agent costs about 15× the tokens** (Anthropic's research-system write-up). Handoffs add failure points and latency. The sponsor also told us to finish two features, not six. It's in the ADR §3 D6 and §14.
3. **"Name the patterns you use."** Prompt chaining with code gates; routing (conditional edges, cached vs live path); parallelisation (four lookups at once in `gather`); evaluator–optimizer (Haiku verifier, one bounded retry); guardrails (injection filter, masking, egress allow-list, fail-closed verifier); tool use over MCP; graceful degradation; human-in-the-loop (the review queue). **ADR §15 maps each one to the code.**
4. **"Where does the model decide anything?"** It writes the brief sentences, judges whether each claim is supported, and extracts commitments from threads. **It never decides which step runs next, and it never calls a tool.**
5. **"Why a separate verifier model?"** A smaller, different model (Haiku) checks each claim against the cited email. That's cheaper and faster, and it isn't the writer grading itself. **If it can't run, claims are removed (fails closed).**
6. **"Why only one retry?"** Unbounded loops break the 30-second limit and can loop forever. One bounded retry, only if time remains, gets most of the quality gain.

## B. Requirement interpretation and fit (15 marks)
7. **"What did the client actually ask for?"** A graph kept current, a pre-meeting brief, quiet alerts. Out of scope by their decision: deal-stage tracking, natural-language querying, a second vertical, any outbound action.
8. **"What did you have to assume?"** What "warm" means, which mailboxes, what goes in a brief, how far back. **We wrote each assumption down** (ADR §12, `docs/client_questions.md`). The second vertical is proven by configuration only (`agency.yaml`), not built.
9. **"Why is quiet detection pure code?"** Partners need to trust and tune it. A formula is free, explainable ("usual gap 12 days, now 94"), reproducible and testable against planted truth. **An AI judgement is none of those.**

## C. Context engineering, MCP and memory (15 marks)
10. **"What's your context strategy?"** A fixed token budget far below the model window, filled by priority: profile → commitments → recent threads in full → older threads as one-line index entries → search hits. Overflow compresses, then drops, and **reports what it trimmed**. Demo: `make big-context`, the heaviest relationship with 667 messages (~70k tokens).
11. **"Why not just use the whole 1M window?"** Cost scales with tokens, and models lose focus in long contexts. A small, relevant context is cheaper, faster and more accurate.
12. **"Why MCP if the model doesn't pick tools?"** It's a **swappable, least-privilege boundary**: a separate service, read-only tools, a database role that can only SELECT. Change Outlook to Gmail and the agent doesn't change. And "kill a tool" is a real failure.
13. **"How do you prove the tools are read-only?"** Tools are annotated read-only, the role `hri_ro` can't INSERT, UPDATE or DELETE (a test tries and fails), and the no-outbound tests scan the code for send-like calls.
14. **"What is your memory?"** Working memory: LangGraph state per brief, checkpointed by run id. Long-term: the graph (people, orgs, edges, commitments, topics), the interaction history and stored briefs. Procedural: the rules in `pe.yaml`. **Stored briefs double as the stale-cache fallback.**

## D. Observability and evidence (15 marks)
15. **"Show me Langfuse."** Every brief is one trace: tags (brief, fallback, escalated, load-test), scores (latency, cost, dropped ratio, citation coverage), and the trace id stored with the brief, which is the audit link. **Langfuse receives masked text and counts only** (fix F02, proven: 0 of 3,211 identifiers in traces).
16. **"What's your latency distribution?"** 🔑 It needs the model key for real briefs. Measured so far: with the AI switched off, 90/90 requests under 6 concurrent users answered, max ~0.6 s, 0 errors. Eric's earlier real-model run: p50 ~11 s on 19 briefs (too few for a true p95). **Be honest: the full load test is the one open item.**
17. **"What's your eval pass rate?"** Eric's last run (before tonight's fixes): 10/35 strict passes, 58% of claims grounded, partly due to a judge bug since fixed and hangs since fixed (F06). The re-run needs the key. **Quiet detection: about 9 in 10 cooled relationships found, all 25 dead deals.**
18. **"Who checked your work?"** The builder never graded himself: an independent evaluator ran every fix (PASS/FAIL with evidence) before it counted.

## E. Constraint compliance, proven (15 marks)
19. **"Prove nothing goes out."** No send tools exist; the database role is read-only; one egress module with a host allow-list; 8 no-outbound tests; the "What leaves the region" tab shows every outbound payload.
20. **"Prove masking works."** The leak test rebuilds real prompts and searches them for every name, email and organisation in the database: 0 hits. **The negative control (masker off) does find leaks, which proves the test works.**
21. **"Prove 30 seconds."** Precompute makes tomorrow's briefs instantly available; the live path has a 25-second internal budget and degrades rather than erroring (F06). 🔑 The live p95/p99 needs the key.
22. **"Human escalation, live."** The "Needs partner review" tab: a flagged brief shows the reason; a partner adds a note and approves or rejects; the decision is logged with a timestamp.
23. **"Does Anthropic train on Hatton's data?"** No: the Commercial Terms say Anthropic "may not train models on Customer Content". The API deletes data within 30 days (batch results 29 days). And they only ever see masked text. (PANEL_PREP §2.5 has the links and dates.)
24. **"Is the data in region?"** **The raw data is**: mail, graph and vault stay in our database. Masked text goes to Anthropic's API. In production the model call moves to an in-region endpoint, which is a configuration change.

## F. Commercial (10 marks): see `docs/explain/cost_model.md` for the full set
25. Backfill $1.08 once; $0.0173 per brief; $221/month normal; 56/68/75% margin at $500/$700/$900; Hatton breaks even at ~4 first-meeting preps a month at $700.
26. **"Your biggest cost?"** The fixed in-region server ($170). AI is ~$7/month. So volume barely moves the bill.

## G. What breaks it (asked of every component)
27. No model key or provider down → facts-only brief, flagged.
28. Data tool down → the last brief, labelled stale.
29. A new name arrives via live ingest → masked only after an API restart (known limitation; runbook).
30. A very short organisation name, or an unknown first name alone → could slip past the dictionary; the NER sweep and the leak test are the safety net.
31. Meetings with several external attendees → only the first is briefed (known limitation).
32. The eval judge is the same model as the writer → self-preference bias; the re-run should use a different judge model.
33. A real inbox is messier than synthetic data (forwards, attachments) → commitment extraction is most exposed.

## H. Team and process
34. **"Who did what?"** Roles in `docs/roles.md`. Eric: Technical Lead and Agent Architect (built the core). Wenura: observability, evals, load test. Yasiru: Commercial Lead (cost model, proposal, verified prices and Anthropic terms) and the 27 Nov review and fix round. **AI assistance is used and disclosed**, as the handbook allows.
35. **"How did you decide what to fix?"** Three independent reviews (code map, running-system test, gap report against our architecture) agreed on the gaps. We fixed in priority order, an evaluator verified each fix, and every decision is logged.
