# hilma-tool

The Finnish reader for the tender radar. It fetches a window of
[Hilma](https://www.hankintailmoitukset.fi) into the delivery shape every country tool in this
family produces, proves the window arrived whole, and says which of its tenders it could actually
read.

Latvia is `eis-tool`, Lithuania `epps-tool`, Estonia `rhr-tool`. One repository per portal, one
country per repository, and the same `day.json` beside the same folders in every one of them — so
a reader that knows one country's layout knows this one.

## One country

`--country FI` is not decoration and there is no default. The destination folder is derived from
the country, so a tool that assumes its own country is a tool that cannot say when it was pointed
at the wrong drive. `country.py` refuses any code it has no reader for, and `deliver_fi.py`
refuses any country that is not Finland even though `country.py` has already refused it — the two
guards answer different questions.

## What is true of Finland and of no other country here

**Hilma is not the main channel, it is the only one.** `hankintalaki` (1397/2016) names
`www.hankintailmoitukset.fi` in the text of the law, for the EU band in 60 § 1 mom and for the
national band in 101 §, and 60 § 5 mom forbids publishing anywhere else first.

**Eleven per cent of tenders can be read and eighty-nine cannot.** The documents of 11% sit on
Hilma's own CDN and download anonymously; the rest are on Cloudia and the other buyer platforms
behind a supplier account, and some tenders have no documents at all. Every one of them still
produces a card, because the metadata is public and the index carries the buyer's own description
at list level. **This is the first country in the family with a tender it cannot read and must
still publish**, which is why `documents_available` — `hilma` / `portal` / `external` / `none` —
travels with every tender all the way to the card. A card judged on a description and a card judged on a
specification are different claims, and nothing downstream can tell them apart otherwise.

**The day-to-day is not where the money is.** Framework call-offs are exempt from the ex-post
notice by name (58 § 5 mom), and where a framework's terms are fixed there is no procurement
decision and no internal report either. A DPS call-off, by contrast, does trigger a notice. So
this radar sees close to 100% of the *entry points* and perhaps a quarter to a half of the annual
euros, and the entry points arrive in rare, high-value bursts — the state property enterprise
re-tenders its 37-lot building, HVAC and electrical frameworks about every five years, next
window 2027/Q1. **Tune this for never missing a framework establishment or a DPS opening, not for
daily volume.**

## Use

```bash
python3 eis_tool.py probe --country FI                        # will this address be told the truth
python3 eis_tool.py discover --date 2026-09-02 --country FI   # the window, fetching nothing
python3 eis_tool.py reconcile --date 2026-09-02 --country FI  # the coverage controls alone
python3 eis_tool.py day 2026-09-02 --country FI --out work    # the window, delivered
python3 eis_tool.py day 2026-08-25 --to 2026-09-02 --country FI --out work   # a catch-up
python3 eis_tool.py fetch 2026-056114 --country FI --out work # one tender by its notice number
python3 eis_tool.py scans --date 2026-09-02 --country FI      # read what no decoder could
python3 eis_tool.py extract --pack work/FI/tenders/36616      # deterministic text
```

Dates are **UTC dates**. `datePublished` is UTC, Helsinki runs two or three hours ahead, and
notices are published as late as 18:36 UTC — a window built from a Helsinki-local "yesterday" and
applied to a UTC field silently drops or duplicates the evening.

## The route

```
GET https://www.hankintailmoitukset.fi/search/eformnotices
```

The portal's own search backend: an anonymous proxy in front of an Azure Cognitive Search index
called `eformnotices-v2`, speaking standard OData. No key, no cookie, no session warm-up. A
document carries 86 fields, including the description, so a whole window can be judged on what
one request returned.

**There is a documented road too**, and it is the one Hilma's terms bless: the `avp-read` product
on the API portal, with a free self-service key. Set `HILMA_SEARCH_URL` and `HILMA_API_KEY` to
take it. The AVP paths are deliberately not written down in this repository — nothing here has
ever called them, and a guessed URL that 404s on the first night is worse than an absent one.

Käyttöehdot (18.5.2026) forbid selling or conveying the material as such to third parties, and
say in the same paragraph that this "does not restrict the User's right to use the information
published in the Service in its own tendering and procurement operations or the use of the
information obtained through the Service's open interfaces for the production of value added
services". The Interfaces page describes the intended shape as taking notices into your own
service, enriching them there and serving your own users from your own system, and asks that the
interface be used sparingly.

## The supplier account

`tarjouspalvelu.fi` (Cloudia, now Mercell) holds 79.6% of Finland's tender documents, and one
supplier account opens all of them. `fi_cloudia.py` signs in once per run with a headless browser,
takes the session's cookies, and downloads each tender's archive with plain HTTP:

```
GET https://tarjouspalvelu.fi/Zip/TarjousPyynnonLiitteet/{tpId}     # tpId = id= of the documents link
```

- **A tender's page is never opened.** Opening one creates a draft response under the company's
  name. The browser sees the front page and the sign-in pages, nothing else.
- **Only tenders somebody wants**: past the recall gate, on the watch list, or fetched by hand.
  The same rule as for Hilma's own documents.
- **The archive is rebuilt on every request**, so its digest changes while its files do not.
  Each member is its own record, keyed by its path in the archive, with its own sha256; the
  archive's digest is not recorded.
- **A tender read this way is `documents_available: portal`**, never `hilma`, so the flag still
  says where the specification came from. One the portal would not give stays `external`, with
  the reason in `index.json` under `portal`.
- **The portal step logs counts only** — no tender number, title or account — because the
  repository and its logs are public. The account arrives in the `CLOUDIA_USERNAME` / `CLOUDIA_PASSWORD` secrets.

`python3 fi_cloudia.py --check` signs in and downloads nothing.

## Five ways this route lies quietly

Every one of these returns HTTP 200.

**1 · It answers a second question with the first question's rows.** Not a 429 — the previous
response, whole, with its own matching `@odata.count`, and the filter that was just sent nowhere
in it. Measured 2026-09-07: six different days asked two seconds apart from a workstation
returned the first day six times. Honest at twenty seconds from that address, and honest at five
from a GitHub runner — the limiter is per address. **So the pace can never be the guard.**
`fi_targets` checks that every row it was handed falls inside the window it asked for and re-asks
with a long wait when one does not, which makes a throttled address slow rather than wrong.
`HILMA_SEARCH_PACE` sets the gap; the default is 6 seconds.

**2 · A dollar lost in transit drops the filter.** OData parameters begin with `$`, and a bare
`$filter` is ignored rather than refused — the endpoint then answers with the default page of the
whole index. Every query here is built through `urlencode`, which percent-encodes it.

**3 · Every rendered page returns the same 9,684-byte shell**, including for ids that were
invented. Status-code link checking is worthless. The detail API
(`/web/api/public/procedure/{pid}/enotice/{nid}`) 404s honestly, so a link is verified by calling
it and matching the notice number — `link_verified` on every index, and a day whose link check
disagreed is short.

**4 · Award notices look exactly like tenders** and are 35% of the volume: title, buyer, CPV,
description. A run that does not separate them publishes a month of decided procurements on its
first night. They are recorded in `changes.json` as closes — the competition an award ends may be
one somebody is still waiting on — and never fetched.

**5 · `estimatedValue: 0.0` with a null currency is not zero.** Read literally it grows a column
of €0 tenders and teaches a reader to distrust the column rather than the rows.

## Three ids and three record shapes

| field | identifies | unique |
|---|---|---|
| `procedureId` | the tender, across its whole life | yes, within eForms |
| `noticeNumber` | the notice a person quotes, `2026-056114` | yes |
| `noticeId` | one notice event | **no** — two id namespaces overlap |

`noticeId 55293` resolves to a 2020 legacy notice while `2026-055293` was a separate eForms
notice. Never key on it.

```
EF-<noticeId>    98%    procedureId and noticeId both real
OLD-<noticeId>   1.3%   procedureId is 0; the key is oldProcurementProjectId, and the link
                        has a different path shape. Still being published — five in four days.
PLAN-<planId>    1%     both ids are 0 and noticeNumber is null
```

A run that assumes `procedureId` is real files every legacy row and every plan under
`tenders/0/`, where each overwrites the last while the day reports them all delivered.
`fi_page.home_id` is what stops that.

**A corrigendum deletes the notice it supersedes** — four for four, and corrigenda are 6.6% of
rows. A stored notice number will find its own row gone; that is normal, and the successor is
found through `procedureId`. A run that reads "row disappeared" as "tender withdrawn" will lie.

## Coverage proof

Three controls, two of which fail the run. `day.json` carries the whole report and
`tools/day_verdict.py` prints it.

| | control | |
|---|---|---|
| 1 | rows held == the index's own `@odata.count` | **hard** |
| 2 | every TED notice for the day found in Hilma by `tedPublicationId` | **hard** |
| 3 | the portal's live-procurement counter within 5% of ours | warn |

**Never compare daily totals with TED.** Hilma leads TED across weekends — 2026-08-30, a Sunday,
carried 72 Hilma notices of which 65 were EU, and TED published them on the Monday. The equality
is per notice, over a ±2-day window, on `tedPublicationId`.

**There is no serial-continuity control, and that was tried.** `noticeNumber` looks like a dense
serial, but it is allocated when a notice is *created*, not when it is published: the hundred
notices published on 2026-09-02 carry numbers spanning 2,819 serials, of which 2,706 belong to
other days. A control that fires 2,706 times on a clean day teaches a reader to ignore controls.

## The recall gate

`policy.py` is the same file in every country tool, and Finland needs no second matcher — but it
does need the policy written differently.

**Four text surfaces are read**, not one: the display title, *every language's title
concatenated*, the buyer's description in every language, and the classification name (always
empty here — Hilma serves codes, not names). 276 of 5,174 rows over three months carry no
Finnish title at all and every one of them carries a title in another language, so the languages
are added together rather than chosen between.

**A term written with spaces around it matches only on a token boundary.** `fold` turns every run
of punctuation into a space and pads the whole text, so ` lvia ` is a whole-token match while
`lvia` stays a substring. This matters more here than anywhere else:

| term | rows matched of 5,174 | why |
|---|---:|---|
| `lvi` | 791 | matches `pilvisiirtymä` — 18 titles in four days |
| ` lvi ` | 70 | |
| `lvia` | 102 | matches `Talvialantie`, a street |
| ` lvia ` | 55 | |

So write the section abbreviations — LVIA, RAU, SÄH, KVR, ST — spaced, and the words bare.

**And mind consonant gradation.** Finnish changes the stem, it does not only add to it:
`huolto` → `huollon`, `hankinta` → `hankinnan`, `urakka` → `urakan`. A stem written to the word
boundary silently misses the inflection a buyer is most likely to use. Stop the stem before the
gradating consonant — `huol`, not `huolto`.

The real policy arrives in the `FI_POLICY` secret and is never committed.
`fi_policy.example.json` is an unrelated illustration, and the nightly workflow refuses to fall
back to it: a night run against it would fetch almost nothing, deliver a valid day, and report
success.

## What gets published

```
work/FI/
  tenders/<pid>/
    procurement.json   every field the index states, in the family shape
    notice.json        the eForms notice as the portal served it
    manifest.json      sha256 per attachment, and the ones the portal would not serve
    doc/<digest>.md    one file per document, named by the digest of its ORIGINAL bytes
    index.json         written LAST — its existence proves the home is whole
    originals/         the downloaded files. Never delivered; the index carries their addresses
    state.json  runs/<date>.json
  <date>/
    day.json           the window, the coverage report, and what could not be proved
    changes.json       what moved, what the gate cut, and what closed
    shards/hilma-day-shard-1/index.json
```

`<pid>` is `procedureId` for an eForms tender, `old-<oldProcurementProjectId>` for a legacy row
and `plan-<planId>` for a plan. The day folder is named for the **last** day of the window,
because the window is how a missed night is caught up. A single hand fetch writes
`<date>-fetch-<number>/` instead, so it can never be mistaken for — or overwrite — a window's.

`index.json` carries the buyer's **description**, and that is deliberate: for the 89% of tenders
whose specification is shut, it is the whole of what a judgement can be made on.

## Running it

`hilma-day.yml` is the night: one runner, no shards. Hilma was asked from eight runner addresses
at one moment on 2026-09-07 and answered all eight with the exact day, so none of Latvia's
address-lottery machinery has anything to do here.

There is **no schedule**, deliberately — the consumer that reads the day starts it, so there is
one actor per day. If one is ever added it **must not be weekday-only**: the batch that forwards
notices to TED runs on Sundays, and 2026-08-30 carried 72 notices against 39 on the Monday.

`hilma-fetch.yml` reads one tender by its number, past the gate, for a card somebody is looking
at right now.

## Tests

```bash
python3 -m unittest discover -s tests -t tests
```

290 of them, none touching the network. The Finnish ones are `test_fi_page.py` (the three record
shapes, and that no home is ever a zero), `test_fi_window.py` (every way discovery lies),
`test_fi_gate.py` (the boundary rule, on the real strings that break it) and `test_fi_day.py`
(what becomes a card and what becomes a close).
