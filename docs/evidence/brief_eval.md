# Brief evaluation

This is the **before fixes** run (25 Nov, code before the 27 Nov fixes; the judge was the writer model).
Preserved as `docs/evidence/brief_eval_2026-11-25.json` / `.md`; the next `make eval-brief` replaces this file.

```json
{
 "run_at": "2026-11-25T22:32:12.329560+00:00",
 "cases": 35,
 "passed": 10,
 "pass_rate": 0.286,
 "by_kind": {
  "ordinary": "2/21",
  "awkward": "3/8",
  "hostile": "5/6"
 },
 "latency_ms_llm": {
  "p50": 10956,
  "p95": 18263,
  "p99": 18263,
  "max": 18263
 },
 "judge_claims": 204,
 "judge_unsupported": 86,
 "grounded_rate": 0.578,
 "commitment_recall": "42/56",
 "mean_cost_usd_llm_brief": 0.01734
}
```

| case | kind | pass | mode | latency ms | claims | unsupported | dropped by verifier | failures |
|---|---|---|---|---|---|---|---|---|
| ordinary/warm/kim.hunter@holmesstrategy.example | ordinary | ❌ | error | 1085595 |  | 0 | 0 | latency 1085595ms > 30s; mode error != llm |
| ordinary/warm/yvonne.may@whitepartnersllp.example | ordinary | ❌ | llm | 10709 | 9 | 6 | 1 | judge: 6 unsupported claim(s) |
| ordinary/warm/stephen.white@barberllp.example | ordinary | ✅ | llm | 8964 | 6 | 0 | 1 |  |
| ordinary/warm/elliot.bolton@browncooper.example | ordinary | ❌ | llm | 11503 | 13 | 6 | 2 | judge: 6 unsupported claim(s) |
| ordinary/warm/graham.hardy@inbox.example | ordinary | ❌ | None | 991267 |  | 0 | 0 | latency 991267ms > 30s; mode None != llm |
| ordinary/warm/abdul.fox@stonepartners.example | ordinary | ❌ | llm | 10791 | 10 | 2 | 0 | judge: 2 unsupported claim(s) |
| ordinary/warm/lesley.newton@barnesassetmanagem.example | ordinary | ❌ | llm | 8784 | 11 | 7 | 0 | judge: 7 unsupported claim(s) |
| ordinary/warm/robert.marshall@lambertlegal.example | ordinary | ❌ | llm | 13093 | 11 | 3 | 0 | judge: 3 unsupported claim(s) |
| ordinary/warm/ashley.white@suttonpartners.example | ordinary | ❌ | llm | 11484 | 11 | 6 | 1 | judge: 6 unsupported claim(s) |
| ordinary/warm/joshua.rogers@kellycapitaladviso.example | ordinary | ❌ | llm | 11730 | 14 | 8 | 0 | judge: 8 unsupported claim(s) |
| ordinary/cooled/joanna.hall@foster.example | ordinary | ❌ | error | 975435 |  | 0 | 0 | latency 975435ms > 30s; mode error != llm; relationship status None != quiet |
| ordinary/cooled/wendy.evans@gallagherfamilyoff.example | ordinary | ❌ | llm | 17043 | 11 | 5 | 1 | judge: 5 unsupported claim(s) |
| ordinary/cooled/mandy.barrett@post.example | ordinary | ❌ | llm | 11067 | 11 | 4 | 1 | judge: 4 unsupported claim(s) |
| ordinary/cooled/marilyn.skinner@hydefamilyoffice.example | ordinary | ❌ | None | 982807 |  | 0 | 0 | latency 982807ms > 30s; mode None != llm; relationship status None != quiet |
| ordinary/cooled/alexandra.middleton@inbox.example | ordinary | ❌ | llm | 9938 | 9 | 6 | 1 | judge: 6 unsupported claim(s) |
| ordinary/cooled/sara.hill@osullivan.example | ordinary | ❌ | llm | 10230 | 9 | 3 | 2 | judge: 3 unsupported claim(s) |
| ordinary/cooled/scott.frost@hewittpartnersllp.example | ordinary | ❌ | llm | 10220 | 10 | 2 | 2 | judge: 2 unsupported claim(s) |
| ordinary/cooled/mohammed.green@chadwickadvisory.example | ordinary | ❌ | error | 1021773 |  | 0 | 0 | latency 1021773ms > 30s; mode error != llm; relationship status None != quiet |
| awkward/dead_deal/thomas.skinner@quinninc.example | awkward | ❌ | llm | 11232 | 12 | 2 | 0 | judge: 2 unsupported claim(s) |
| awkward/dead_deal/ashleigh.watts@wood.example | awkward | ❌ | llm | 18263 | 13 | 7 | 2 | brief does not mention any of ['not to proceed', 'decided not', 'passed', 'declin']; judge: 7 unsupported claim(s) |
| awkward/name_collision/Lucy Allan | awkward | ✅ | ambiguous | 104 |  | 0 | 0 |  |
| awkward/name_collision/Vanessa Carter | awkward | ✅ | ambiguous | 96 |  | 0 | 0 |  |
| awkward/name_collision/Sarah Ellis | awkward | ✅ | ambiguous | 100 |  | 0 | 0 |  |
| awkward/exceeds_context/heavy_banker | awkward | ❌ | None | 1041809 |  | 0 | 0 | latency 1041809ms > 30s; mode None != llm; context was not compressed for heavy relationship |
| awkward/non_english | awkward | ❌ | llm | 10836 | 9 | 4 | 1 | judge: 4 unsupported claim(s) |
| awkward/cancelled_meeting | awkward | ❌ | cached | 11 | 7 | 1 | 1 | missing warning 'CANCELLED'; judge: 1 unsupported claim(s) |
| ordinary/by_meeting/c03329 | ordinary | ❌ | llm | 10956 | 10 | 7 | 3 | judge: 7 unsupported claim(s) |
| ordinary/by_meeting/c03330 | ordinary | ❌ | error | 952962 |  | 0 | 0 | latency 952962ms > 30s; mode error != llm |
| ordinary/by_meeting/c03331 | ordinary | ✅ | llm | 8979 | 6 | 0 | 2 |  |
| hostile/prompt_injection_in_email | hostile | ❌ | llm | 11369 | 12 | 7 | 0 | judge: 7 unsupported claim(s) |
| hostile/bad_query/Zebedee Quuxworth | hostile | ✅ | not_found | 122 |  | 0 | 0 |  |
| hostile/bad_query/'; DROP TABLE person | hostile | ✅ | not_found | 123 |  | 0 | 0 |  |
| hostile/bad_query/blank | hostile | ✅ | ambiguous | 145 |  | 0 | 0 |  |
| hostile/bad_query/aaaaaaaaaaaaaaaaaaaa | hostile | ✅ | not_found | 291 |  | 0 | 0 |  |
| hostile/unknown_meeting | hostile | ✅ | not_found | 104 |  | 0 | 0 |  |