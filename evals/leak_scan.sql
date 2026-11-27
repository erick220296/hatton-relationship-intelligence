-- Leak scan: does any real name, email or org name appear in anything we sent out of the region?
-- Run: docker compose exec -T db psql -U hri -d hri -f - < evals/leak_scan.sql
-- Expected: every "leaked" count is 0 (an org literally named "Unknown" matches the English word; that hit is a false positive).
\echo '--- egress summary (only api.anthropic.com should appear) ---'
select host, allowed, count(*) as requests, min(at) as first, max(at) as last
from egress_audit group by host, allowed order by host;

\echo '--- real identifiers found in outbound request bodies ---'
with bodies as (select string_agg(lower(coalesce(request_body, '')), ' ') as b from egress_audit where allowed),
ents as (
  select 'person full name' as kind, lower(name) as v from person where name like '% %'
  union select 'email', lower(email) from person where email is not null
  union select 'org name', lower(name) from org where length(name) >= 6)
select kind, count(*) as checked, count(*) filter (where position(v in b) > 0) as leaked
from ents, bodies group by kind order by kind;

\echo '--- which ones (should be empty, apart from the "Unknown" false positive) ---'
with bodies as (select string_agg(lower(coalesce(request_body, '')), ' ') as b from egress_audit where allowed),
ents as (
  select lower(name) as v, name as real from person where name like '% %'
  union select lower(email), email from person where email is not null
  union select lower(name), name from org where length(name) >= 6)
select real from ents, bodies where position(v in b) > 0;
