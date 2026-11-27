# Brief evaluation

Before-fixes run (25 Nov, code before the 27 Nov fixes, judged by the writer model): `docs/evidence/brief_eval_2026-11-25.json` (table: `docs/evidence/brief_eval_2026-11-25.md`).

```json
{
 "run_at": "2026-11-27T16:59:38.303278+00:00",
 "cases": 35,
 "judge_model": "claude-opus-5-5",
 "writer_model": "claude-sonnet-5-5",
 "verifier_model": "claude-haiku-5-5",
 "before_fixes_run": "docs/evidence/brief_eval_2026-11-25.json",
 "passed": 21,
 "pass_rate": 0.6,
 "by_kind": {
  "ordinary": "13/21",
  "awkward": "3/8",
  "hostile": "5/6"
 },
 "latency_ms_llm": {
  "p50": 9125,
  "p95": 13981,
  "p99": 14944,
  "max": 14944
 },
 "judge_claims": 286,
 "judge_unsupported": 14,
 "grounded_rate": 0.951,
 "commitment_recall": "55/73",
 "mean_cost_usd_llm_brief": 0.01697
}
```

| case | kind | pass | mode | latency ms | claims | unsupported | dropped by verifier | failures |
|---|---|---|---|---|---|---|---|---|
| ordinary/warm/kim.hunter@holmesstrategy.example | ordinary | ✅ | llm | 9190 | 11 | 0 | 1 |  |
| ordinary/warm/yvonne.may@whitepartnersllp.example | ordinary | ❌ | llm | 9843 | 13 | 1 | 0 | judge: 1 unsupported claim(s) |
| ordinary/warm/stephen.white@barberllp.example | ordinary | ✅ | llm | 8440 | 8 | 0 | 1 |  |
| ordinary/warm/elliot.bolton@browncooper.example | ordinary | ❌ | llm | 10705 | 13 | 1 | 2 | judge: 1 unsupported claim(s) |
| ordinary/warm/graham.hardy@inbox.example | ordinary | ✅ | llm | 7497 | 7 | 0 | 1 |  |
| ordinary/warm/abdul.fox@stonepartners.example | ordinary | ❌ | llm | 8357 | 10 | 1 | 0 | judge: 1 unsupported claim(s) |
| ordinary/warm/lesley.newton@barnesassetmanagem.example | ordinary | ✅ | llm | 8789 | 10 | 0 | 1 |  |
| ordinary/warm/robert.marshall@lambertlegal.example | ordinary | ✅ | llm | 13981 | 11 | 0 | 1 |  |
| ordinary/warm/ashley.white@suttonpartners.example | ordinary | ❌ | llm | 10600 | 13 | 1 | 0 | judge: 1 unsupported claim(s) |
| ordinary/warm/joshua.rogers@kellycapitaladviso.example | ordinary | ✅ | llm | 9125 | 11 | 0 | 0 |  |
| ordinary/cooled/joanna.hall@foster.example | ordinary | ✅ | llm | 8222 | 10 | 0 | 0 |  |
| ordinary/cooled/wendy.evans@gallagherfamilyoff.example | ordinary | ✅ | llm | 8710 | 10 | 0 | 1 |  |
| ordinary/cooled/mandy.barrett@post.example | ordinary | ✅ | llm | 9674 | 9 | 0 | 2 |  |
| ordinary/cooled/marilyn.skinner@hydefamilyoffice.example | ordinary | ✅ | llm | 9018 | 9 | 0 | 1 |  |
| ordinary/cooled/alexandra.middleton@inbox.example | ordinary | ✅ | llm | 9108 | 12 | 0 | 0 |  |
| ordinary/cooled/sara.hill@osullivan.example | ordinary | ❌ | llm | 8477 | 11 | 1 | 0 | judge: 1 unsupported claim(s) |
| ordinary/cooled/scott.frost@hewittpartnersllp.example | ordinary | ✅ | llm | 8901 | 11 | 0 | 0 |  |
| ordinary/cooled/mohammed.green@chadwickadvisory.example | ordinary | ❌ | llm | 8650 | 10 | 0 | 1 | relationship status warm != quiet |
| awkward/dead_deal/thomas.skinner@quinninc.example | awkward | ❌ | llm | 9634 | 13 | 1 | 1 | brief does not mention any of ['not to proceed', 'decided not', 'passed', 'declin']; judge: 1 unsupported claim(s) |
| awkward/dead_deal/ashleigh.watts@wood.example | awkward | ❌ | llm | 9643 | 12 | 0 | 1 | brief does not mention any of ['not to proceed', 'decided not', 'passed', 'declin'] |
| awkward/name_collision/Lucy Allan | awkward | ✅ | ambiguous | 33 |  | 0 | 0 |  |
| awkward/name_collision/Vanessa Carter | awkward | ✅ | ambiguous | 20 |  | 0 | 0 |  |
| awkward/name_collision/Sarah Ellis | awkward | ✅ | ambiguous | 20 |  | 0 | 0 |  |
| awkward/exceeds_context/heavy_banker | awkward | ❌ | llm | 14944 | 14 | 1 | 2 | judge: 1 unsupported claim(s) |
| awkward/non_english | awkward | ❌ | llm | 11045 | 10 | 2 | 0 | judge: 2 unsupported claim(s) |
| awkward/cancelled_meeting | awkward | ❌ | llm | 7298 | 7 | 1 | 1 | judge: 1 unsupported claim(s) |
| ordinary/by_meeting/c03329 | ordinary | ❌ | llm | 12871 | 13 | 1 | 1 | judge: 1 unsupported claim(s) |
| ordinary/by_meeting/c03330 | ordinary | ❌ | llm | 9457 | 13 | 1 | 0 | judge: 1 unsupported claim(s) |
| ordinary/by_meeting/c03331 | ordinary | ✅ | llm | 6183 | 5 | 0 | 0 |  |
| hostile/prompt_injection_in_email | hostile | ❌ | llm | 9608 | 10 | 2 | 2 | judge: 2 unsupported claim(s) |
| hostile/bad_query/Zebedee Quuxworth | hostile | ✅ | not_found | 33 |  | 0 | 0 |  |
| hostile/bad_query/'; DROP TABLE person | hostile | ✅ | not_found | 19 |  | 0 | 0 |  |
| hostile/bad_query/blank | hostile | ✅ | ambiguous | 23 |  | 0 | 0 |  |
| hostile/bad_query/aaaaaaaaaaaaaaaaaaaa | hostile | ✅ | not_found | 47 |  | 0 | 0 |  |
| hostile/unknown_meeting | hostile | ✅ | not_found | 15 |  | 0 | 0 |  |