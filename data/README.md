# Data files

Everything here is written by the pipeline. Don't edit these files by hand; changes are overwritten on the next run. Dollar amounts are in dollars, dates are ISO format (YYYY-MM-DD) unless they come straight from the FEC (MM/DD/YYYY).

| File | What's in it |
|---|---|
| `members.json` | Every current senator and House member: Bioguide ID, name, party, state, district, FEC candidate IDs, website, contact form, DC phone and address, and district offices. Use it for the "contact your reps" feature. |
| `votes/index.json` | Every roll-call vote kept (newest first): chamber, date, question, kind (`passage`, `amendment`, `procedural`, `cloture`, `nomination`, `resolution`, `final_action`, `other`), result, bill, totals and party breakdown. |
| `votes/house/{congress}-{session}-{number}.json`, `votes/senate/...` | One vote, including how every member voted. |
| `members/{bioguide}/votes.json` | One member's votes, newest first, plus `latest_passage_vote`. |
| `bills/{type}{number}-{congress}.json` | Title, short title, sponsor, latest action, whether it became law, the full CRS summary, a one-sentence summary, and a Congress.gov link. |
| `upcoming.json` | Bills on the House floor schedule for this week and next, and bills expected on the Senate floor (the Senate posts no weekly list, so these come from recent cloture votes and Congress.gov bill actions). |
| `members/{bioguide}/money.json` | Campaign money: `summary` (FEC totals), `pac_contributions` (every committee that gave, with totals), and `detail` (small vs. large donor split, where donors live, where donors work, principal committee). |
| `rankings.json` | Top fundraisers by chamber and party. |
| `races.json` | Every candidate who filed FEC reports this cycle, grouped by race (for example `GA-SEN`, `TX-33`), with receipts and cash on hand. |
| `lobbying/{type}{number}-{congress}.json` | Lobbying reports that name the bill, each client with its report count and latest report amount, its PACs, and the groups chosen for display. |
| `districts/index.json` | Every district: member, Polsby-Popper score, square miles, and least-compact ranks (overall and by party). |
| `districts/{code}.json` | One district: score, member, an SVG outline (480 × 360) and the counties it overlaps, drawn in the same box. |
| `districts/shapes/{code}.geojson` | One district's boundary (simplified to about 200 meters), for maps. |
| `districts/lookup.geojson` | All districts in one smaller file, for finding the district that contains a point (an address after geocoding). |
| `state/` | Bookkeeping so each run only fetches new votes. |
| `status_daily.json`, `status_districts.json` | What the last run did: each step, whether it worked, errors, and request counts. |

The pre-launch page data is written to `../site/landing-data.js` and `../site/index.html`.
