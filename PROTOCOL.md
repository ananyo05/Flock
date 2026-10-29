How the Urja Meter Ops portal works
Everything below was discovered by using the portal as a normal logged-in user: reading the SvelteKit JS bundles served to the browser, watching network requests, and probing with curl. Nothing was attacked or modified; all calls are read-only GETs plus the login POST.
Items marked (unverified) are inferred from the client code or a small sample and should be re-checked before relying on them.
1. Stack at a glance
Aspect	Finding
Frontend	SvelteKit (Svelte 5) SPA with SSR, Tailwind CSS
Auth library	better-auth (cookie name __Secure-better-auth.session_token; sign-out at /api/auth/sign-out)
Two kinds of data endpoints	(a) JSON endpoints under /portal/*; (b) SvelteKit __data.json page-data endpoints
Data scope seen	403 meters (J100000..J100402), 40 distribution transformers (DT-001..DT-040)
2. Authentication and session
POST /login is a SvelteKit form action. The browser sends application/x-www-form-urlencoded with fields email and password, and Accept: application/json. Header x-sveltekit-action: true is set by the SvelteKit form enhancer.
On success it returns 200 and a Set-Cookie: __Secure-better-auth.session_token=<token>.<signature>; Max-Age=3600; Path=/; HttpOnly; Secure; SameSite=Lax
Every later call just needs that cookie (curl -b cookies.txt). No bearer token, no CSRF header was needed for GETs.
Session lifetime is 1 hour (Max-Age=3600). A long-running client must re-login. I did not observe a sliding refresh (unverified). Detect expiry by a non-200 or a redirect/HTML response to /login and re-authenticate once, then retry.
The __Secure- prefix means the cookie is only sent over HTTPS; cookie jars must honour Secure.
Logout: POST /api/auth/sign-out, then redirect to /login.
Unauthenticated requests to data endpoints: not exhaustively tested (unverified). Treat anything that isn't 200 JSON as "session invalid".
3. Page routes (UI)
/ and /login (public); /meters, /meters/[id], /transformers (under the (portal) layout, login required).
4. JSON endpoints under /portal
Send Accept: application/json.
4.1 GET /portal/meters/search?q=<text>&page=<n>
Response:
json
{"data":[{"meterId":"J100004","serialNo":"SE65293","make":"HPL","phaseType":"single","installStatus":"Faulty","dtCode":"DT-005"}],
 "total":403,"page":1,"pageSize":20}
Fixed page size of 20, 1-based pages. 403 meters means 21 pages.
q is a case-insensitive substring match on meter ID or serial number. Verified: J100004 returns 1; 100004 (no J) returns 1; J100 returns all 403; SE65293 and se65293 both match; SE6 returns 7.
Empty q lists everything. This is the only way to enumerate meters through the JSON API (I walked the 21 pages into meters_all.jsonl).
Serial numbers can contain & (e.g. L&34861), so URL-encode q.
Behaviour for page out of range: not tested (unverified).
4.2 GET /portal/meters/{id}/geo
json
{"data":{"latitude":"26.983252940494662","longitude":"75.88163262899984"}}
Coordinates are strings, around Jaipur (about 26.9N, 75.8E).
4.3 GET /portal/meters/{id}/energy[?from=&to=]
json
{"data":[{"timestamp":"23/06/2026 00:00","kwh":"37562.9","kvah":"40567.93","voltR":"225"}]}
All values are strings. The UI treats empty values as missing (kwh || "—"), so expect blank fields.
timestamp is DD/MM/YYYY HH:mm, with no timezone (presumably local, IST). Parse explicitly; it is easy to confuse with MM/DD.
kwh / kvah are cumulative register readings (monotonic), not per-interval consumption. Consumption means differencing consecutive readings. For J100049 the daily delta was about 0.42 to 0.43 kWh with no negatives in the sample. Meter replacement or rollover could cause negative deltas (unverified).
voltR is the R-phase voltage in volts.
Reading granularity differs by meter. In the sample, J100000 and J100025 return half-hourly data (337 rows by default). Every other sampled meter (J100049, J100050, J100075, ... J100402) returns daily readings at 00:00 (8 rows by default). Which meters are half-hourly is not exposed by any field I found; the boundary is unverified.
The last reading in the dataset is 30/06/2026 23:30 (half-hourly) or 30/06/2026 00:00 (daily). The data is a frozen snapshot, not live.
from / to query parameters (the quirky part):
Input	Result
No params	Default window of 7 days ending at the latest reading (337 half-hourly rows, 8 daily rows)
from=2026-06-25	Parsed (ISO date). Data from 25/06 00:00 to the end (288 rows)
from=2026-06-20 only	Window of from + 7 days: 20/06 00:00 to 27/06 00:00 (337 rows)
from=2026-06-24 only	24/06 00:00 to 30/06 23:30 (336 rows, clipped by data end)
to=2026-06-30 only	The 7 days ending at the end of that day: 24/06 00:00 to 30/06 23:30
to=2026-06-26 only	20/06 00:00 to 26/06 23:30
from=2026-06-24&to=2026-06-26	144 rows (3 days, to is inclusive of the whole day)
from=2026-06-01&to=2026-06-30	1440 rows (30 days). No 7-day cap when both are given, and data exists back to at least 1 June
Daily meter, from=2026-06-26	5 rows (26 to 30 June)
from=23/06/2026, from=25/06/2026 00:00, from=2026-06-25T00:00:00Z, to=30/06/2026	Silently ignored: falls back to the default window. No error
to=2027-01-01	0 rows (consistent with default "to minus 7 days" landing where no data exists, unverified as the cause)
Summary: only plain YYYY-MM-DD dates are understood. Anything else is silently ignored and returns the default window, which is a trap because the response looks valid. A wrapper should validate dates itself and reject bad input rather than forwarding it.
4.4 GET /portal/dts?page=<n>
{"data":[{"code":"DT-007","name":"Old Malviya Nagar Xfmr","feederCode":"F-007","capacityKva":"..."}],"total":40}. 20 per page, so 2 pages. Field names are from the client code; exact value types are unverified.
5. Meter detail via SvelteKit __data.json
GET /meters/{id}/__data.json returns SvelteKit's devalue serialisation: a flat array in which objects refer to other array slots by index. It is not plain JSON. Node [2] holds the page data: {meterId, detail, hierarchy}. Decode with the devalue package (parse/unflatten) or a small index-resolving function; do not read it by eye.
detail has two shapes (the UI handles both):
{data:[{parameterName, parameterValue}, ...]}: a list of label/value pairs: Meter ID, Serial No, Make, Phase Type, Installation Status, Installation Type.
{classData: "<JSON string>"}: a string that must be JSON.parsed, with the fields under an installed_meter object.
I started counting how many meters use shape 2 (grep -l classData) but the run was interrupted, so the proportion is unknown. Parsers must handle both.
hierarchy is a flat map of display strings in the form "Name (CODE)": Zone, Circle, Division, Subdivision, Sub Station, Feeder, DT, plus Meter ID, Installation Status, Installation Type. Example for J100399: Jaipur Zone 1 (Z-01) > Circle 4 (C-04) > Division 10 (D-10) > Subdivision 12 (SD-12) > Substation 4 (SS-04) > Feeder 12 (F-012) > Civil Lines DT 40 (DT-040).
Values here that the search endpoint does not give you: Installation Type (Whole Current, CT Operated) and the full hierarchy. Also, dtCode in search results is a bare code while the hierarchy has the Name (CODE) form; parse the code out with a regex such as \(([^)]+)\)$.
Bulk-fetching detail means one request per meter (403 requests). I used 0.2 to 0.3s sleeps and saw no throttling, but no rate-limit documentation exists (unverified).
6. Bulk export path (signed requests)
The Transformers page has an "Export all meters" button that reveals a second, bulk route:
GET /portal/keys returns {"data":{"signingSecret":"<secret>"}}. The secret is handed to any logged-in session.
The client computes an HMAC-SHA256 (hex) over four newline-joined parts: METHOD \n PATH \n QUERYSTRING \n TIMESTAMP e.g. GET\n/portal/export\npage=1\n1790000000, keyed with the secret string as UTF-8. TIMESTAMP is Unix seconds.
GET /portal/export?page=1 with headers x-timestamp: <ts> and x-signature: <hex>.
Response: {"data":[...meters...],"total":N}. The UI downloads data as meter-export.json and reports Exported {total} meters.
Open questions (all unverified; this path is only read from client code and not yet exercised):
Does page=1 return all meters or only the first page? The UI only ever requests page 1 yet reports total, so it may be silently truncating. Compare data.length to total and try page=2.
Shape of each exported record (likely richer than the search rows).
Allowed clock skew for x-timestamp, and whether the signature must cover the exact query string order.
The signing secret may rotate, so refetch /portal/keys after a failure (and after re-login).
7. Quirks and surprises (summary)
Silent date handling: invalid from/to formats are ignored with a 200 and a plausible-looking 7-day window (section 4.3).
Mixed granularity: some meters half-hourly, most daily; nothing flags which.
Cumulative readings and everything a string; blanks possible.
No timezone and DD/MM/YYYY timestamps.
Two detail shapes, one hiding JSON inside a string (classData).
__data.json is index-based devalue, not JSON you can query directly.
Hierarchy encoded as "Name (CODE)" strings, and the numbers inside codes do not reliably nest (e.g. J100399: Circle 4 / Division 10 / Subdivision 12 / Substation 4 / Feeder 12). Do not derive the tree from code arithmetic; build it from observed parent-child pairs.
Inconsistent assignment: J100400, J100401, J100402 (three-phase, Genus, CT Operated) all sit under DT-007 even though the other DT-007 meters follow a stride-of-40 ID pattern (J100006, J100046, ... J100366). They were evidently appended after the base 400 meters. Expect more such irregularities when reconstructing the hierarchy.
Serials contain & and need encoding.
The signing secret is served to every session and the export path is HMAC-signed (see section 6).
Session is 1 hour with no observed refresh.
The meters list first renders "0 total" in server HTML and only fills after client-side fetch; scraping HTML is useless, so use the JSON endpoints.
8. Enumerated values seen
make: HPL, Genus, L&T, Allied, Secure
phaseType: single, three
installStatus: Installed, Faulty, Decommissioned
Installation Type (detail only): Whole Current, CT Operated
DT codes: DT-001..DT-040 (40 distinct)
9. Recommended access strategy for the wrapper service
Log in once, keep the cookie, and re-login on any auth failure or shortly before the 1-hour expiry.
Enumerate meters via search?q= over all pages (or the export path once validated), then enrich with __data.json (detail + hierarchy), /geo, and /energy on demand.
Normalise on the way out: numbers as numbers, ISO-8601 timestamps with an explicit (assumed IST) offset, consumption as deltas, hierarchy as a structured tree.
Validate from/to yourself and require YYYY-MM-DD.
Cache meter metadata (slow-changing) and treat energy as short-lived or on-demand.
Throttle politely (a small delay between requests, bounded concurrency).