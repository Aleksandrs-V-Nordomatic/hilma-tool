"""The Finnish source: Hilma's notice index, read as OData, and one notice read whole.

Hilma does not have to be parsed. The portal is a single-page application over an Azure
Cognitive Search index (`eformnotices-v2`, 157 981 documents on 2026-09-05), and that index
answers standard OData directly: the fields arrive named and typed, eighty-six of them, and a
restyled page does not move any of them.

TWO ROADS TO THE SAME INDEX, AND THE TOOL TAKES EITHER.

    the portal's own backend   GET /search/eformnotices            no key, no cookie
    the documented interface   avp-read, on Azure API Management   free self-service key

The second is the one Hilma's own terms bless. Kayttoehdot (18.5.2026) forbid selling or
conveying the material as such to third parties and then say, in the same paragraph, that this
"does not restrict the User's right to use the information published in the Service in its own
tendering and procurement operations or the use of the information obtained through the
Service's open interfaces for the production of value added services" -- and the Interfaces page
describes the intended shape as taking notices into your own service, enriching them there and
serving your own users from your own system. That is this radar, named.

So the anonymous road is the default because it is what has been measured end to end, and the
keyed road is a matter of setting two environment variables when somebody registers. The AVP
paths are NOT written down here, deliberately: nothing in this repository has ever called them,
and a guessed URL that returns 404 on the first night is worse than an absent one. Read them off
the API portal at registration and set `HILMA_SEARCH_URL`.

THE ONE MISTAKE THAT RETURNS 200 AND THE WRONG DATA. OData's parameters begin with a dollar
sign, and a `$filter` whose dollar is lost in transit is not refused -- it is ignored, and the
endpoint answers with the default page of the whole index. Plausible JSON, correct status, wrong
century. `urllib.parse.urlencode` percent-encodes the dollar as `%24`, which is why every request
in this file is built through it and none is built by joining strings.

THREE RECORD SHAPES IN ONE INDEX, AND THEY ARE NOT INTERCHANGEABLE:

    EF-<noticeId>    98%   eForms. procedureId and noticeId both real.
    OLD-<noticeId>   1.3%  legacy, still being published. procedureId is 0; the key is
                           oldProcurementProjectId, and the link has a different shape.
    PLAN-<planId>    1%    a procurement plan. Both ids are 0 and noticeNumber is null.

A run that assumes `procedureId` is real files every legacy row under `tenders/0/` and builds
every legacy link out of zeros -- which, on this portal, opens a page that renders rather than
one that fails. See `home_id` and `view`.

    python3 fi_page.py 2026-056114        # one notice and its attachments, as JSON
"""
import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse

import net

BASE = "https://www.hankintailmoitukset.fi"
CDN = "https://cdn.hankintailmoitukset.fi"

# The portal's own search backend: an anonymous proxy in front of the search index. Overridable
# because the documented `avp-read` product speaks the same OData against the same index, and
# moving to it should be a deployment change rather than an edit here.
SEARCH = os.environ.get("HILMA_SEARCH_URL") or (BASE + "/search/eformnotices")
# Set only when the run holds a registered key. Absent means the anonymous road.
API_KEY = os.environ.get("HILMA_API_KEY") or None

# The address that discriminates. Every RENDERED page on this portal answers 200 with the same
# 9 684-byte shell, including for ids that were invented, so a link is never verified by status.
# This one 404s honestly and carries the notice.
DETAIL = BASE + "/web/api/public/procedure/%s/enotice/%s"

# What a person clicks, per record shape. `procedure`, singular, and the legacy shape is a
# different path with a different id in it -- not the same URL with another number.
VIEW_EFORMS = BASE + "/fi/public/procedure/%s/enotice/%s"
VIEW_LEGACY = BASE + "/fi/public/procurement/%s/notice/%s/overview"
VIEW_PLAN = BASE + "/fi/public/procurementplan/%s"

# One attachment, by the GUID the detail JSON carries. Anonymous, and proved twice end to end.
ATTACHMENT = CDN + "/public-attachments/%s"

# An attachment the portal is willing to serve. Every attachment observed carried 10; the value
# is checked rather than assumed because a notice whose files are still being processed would
# otherwise be downloaded as a run of error pages.
ATTACHMENT_READY = 10

# The tool family's name, not this country's portal, and deliberately the same string every
# country fork sends: it identifies the client to an operator who asks and discloses nothing
# about who is running it.
UA = "Mozilla/5.0 (compatible; eis-tool)"

# TWO PACES, BECAUSE THE TWO ENDPOINTS MISBEHAVE DIFFERENTLY, AND THIS IS MEASURED.
#
# The search index does not refuse a request it does not want to answer. It answers it with the
# PREVIOUS request's rows -- same records, same `@odata.count`, HTTP 200, and the filter that was
# sent nowhere in the reply. Asked for six different days two seconds apart from a workstation on
# 2026-09-07 it returned the first day six times. At twenty seconds it was honest six times out
# of six. From a GitHub runner, five seconds was honest six times out of six: the limiter is per
# address, and the address that has been asking all week is the one it has stopped answering.
#
# The detail API does none of this: six different notices at one-second intervals returned six
# different notices. Which is what makes a day affordable -- the expensive pace applies to one
# request per day of window, not to one per tender.
#
# THE PACE IS NOT WHAT MAKES THE RUN CORRECT. `fi_targets` checks that every row it was handed
# falls inside the window it asked for and re-asks when one does not, so a throttled address
# makes a run slow rather than wrong. This number decides how long a catch-up takes.
SEARCH_PACE = float(os.environ.get("HILMA_SEARCH_PACE") or 6.0)
DETAIL_PACE = (0.4, 1.2)

# An award notice looks exactly like a tender -- title, buyer, CPV, description -- and they are
# 35% of the volume. A run that does not separate them publishes a month of already-decided
# procurements on its first night.
AWARD_TYPES = ("ContractAwardNotices",)
PLAN_TYPES = ("ProcurementPlan",)
CONSULTATION_TYPES = ("PriorInformationNotices",)
# `Sopimusmuutos` -- a change to a contract that was already awarded. Nothing to bid for, and it
# is the one row on the measured day that is neither a competition nor an award.
MODIFICATION_TYPES = ("Modification",)

# THE POPULATIONS A CARD CAN COME FROM. Everything else is history: an award closes a
# competition, a modification changes a contract somebody else already won. Publishing either
# as a new opportunity is the failure this route makes easiest -- awards are 35% of the volume
# and carry a title, a buyer, a CPV and a description exactly like a live tender does.
LIVE_KINDS = ("tender", "amendment", "consultation", "plan")

class Refused(RuntimeError):
    """The portal answered, and the answer was that this notice is not there.

    A type rather than a string, so a caller can tell it from "we could not ask" -- which
    `net.Unreachable` is -- without matching on a message. The two must never be recorded the
    same way: a notice we could not reach is a gap in the run, a notice the portal says is gone
    is a fact about the tender, and on this portal it is usually a corrigendum having superseded
    it rather than a withdrawal.
    """


# One clock per endpoint. Sharing one would make every detail request wait out the search pace
# and turn a day of sixty tenders into six minutes of sleeping for no reason.
_last = {"search": 0.0, "detail": 0.0}


def log(message):
    print(message)
    sys.stdout.flush()


def _pace(which, seconds=None):
    """Wait out the gap since the previous request to this endpoint, whoever made it.

    The jitter is not decoration: a fixed gap from a fixed start is a pattern, and Hilma's own
    Interfaces page asks that the interface be used sparingly.
    """
    if seconds is None:
        seconds = (SEARCH_PACE if which == "search" else random.uniform(*DETAIL_PACE))
    if which == "search":
        seconds = seconds * random.uniform(1.0, 1.25)
    gap = seconds - (time.time() - _last[which])
    if gap > 0:
        time.sleep(gap)
    _last[which] = time.time()


def _headers():
    head = {"User-Agent": UA, "Accept": "application/json"}
    if API_KEY:
        head["Ocp-Apim-Subscription-Key"] = API_KEY
    return head


def search(params, timeout=60):
    """One OData query against the notice index. Returns the parsed answer.

    `params` are given without their dollars -- `{"filter": ..., "top": 1000}` -- and the dollars
    are added here, once, in the one place that also percent-encodes them. A caller that could
    spell a parameter itself is a caller that can spell it without its dollar, and that failure
    returns the whole index under a 200.
    """
    query = {}
    for key, value in params.items():
        if value is None:
            continue
        query[key if key == "search" else "$%s" % key] = value
    _pace("search")
    url = "%s?%s" % (SEARCH, urllib.parse.urlencode(query))
    body, _ = net.get_json(url, headers=_headers(), timeout=timeout, log=log)
    return body


def detail(procedure_id, notice_id, timeout=60):
    """The notice document, whole, from the address that tells the truth about whether it exists.

    Returns the parsed detail document, or None when the portal says 404 -- which it does
    correctly here and nowhere else. A caller must treat None as "no such notice", never as
    "could not ask": `net` raises `Unreachable` for the second, and the two must not be recorded
    the same way.
    """
    _pace("detail")
    try:
        body, _ = net.get_json(DETAIL % (procedure_id, notice_id), headers=_headers(),
                               timeout=timeout, log=log)
        return body
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def shape_of(row):
    """`eforms`, `legacy` or `plan`, read off the index row's own id."""
    identity = str(row.get("id") or "")
    if identity.startswith("OLD-"):
        return "legacy"
    if identity.startswith("PLAN-"):
        return "plan"
    return "eforms"


def kind_of(row):
    """Which population a row belongs to. A reader asks a different question of each.

        tender        a competition somebody can bid for.
        consultation  a prior information notice -- 19% of the volume, and `tietopyynto` is the
                      commonest stem in the language. Future intent, sometimes a market
                      consultation, and answering one is how a specification comes to describe
                      something anybody can deliver.
        plan          a procurement plan. Intent, months out, and no notice yet.
        award         the competition is over. Never publish one as new.
        modification  a change to a contract already awarded. Nothing to bid for.
        amendment     a corrigendum. It supersedes and DELETES the notice it replaces, so it is
                      news about a tender already on the board, not a second tender.

    ORDER MATTERS HERE. A corrigendum to an award notice is still an award, and asking about the
    corrigendum flag first would file it as an amendment and put a decided procurement in front
    of somebody as a change worth reading.
    """
    main = row.get("mainType")
    if main in PLAN_TYPES or row.get("isPlan"):
        return "plan"
    if main in AWARD_TYPES:
        return "award"
    if main in MODIFICATION_TYPES:
        return "modification"
    if row.get("isCorrigendum"):
        return "amendment"
    if main in CONSULTATION_TYPES:
        return "consultation"
    return "tender"


def is_live(row_or_record):
    """Could somebody still bid for this? The question the day counts on.

    Takes an index row or a built record, because both callers exist and getting the two
    confused is how an award gets counted as an opportunity.
    """
    kind = row_or_record.get("kind") if "kind" in row_or_record else kind_of(row_or_record)
    return kind in LIVE_KINDS


def home_id(row):
    """The folder this row's tender lives in, for ever, and it is never a zero.

    `procedureId` is the tender's identity and the whole delivery is keyed on it -- but it is 0
    on every legacy row and on every plan, and those are 2.3% of a live day rather than history.
    Keyed naively, all of them would share `tenders/0/`: each would overwrite the last, the day
    would report them all delivered, and the folder would hold whichever came last.
    """
    shape = shape_of(row)
    if shape == "legacy":
        return "old-%s" % (row.get("oldProcurementProjectId") or row.get("noticeId"))
    if shape == "plan":
        return "plan-%s" % (row.get("planId") or str(row.get("id") or "").split("-")[-1])
    pid = row.get("procedureId")
    if not pid:
        # An eForms row with no procedure id has never been seen. If one arrives, it gets its
        # own folder from the index row's own id rather than sharing `tenders/0/` with the next.
        return "row-%s" % (row.get("id") or row.get("noticeId"))
    return str(pid)


def view(row):
    """The URL a person opens. Three shapes, because the portal has three.

    Never verified by status: see the module docstring. `fi_targets.verify` calls the detail API
    for the same id pair and matches the notice number, which is the only check this portal can
    actually fail.
    """
    shape = shape_of(row)
    if shape == "legacy":
        return VIEW_LEGACY % (row.get("oldProcurementProjectId"), row.get("noticeId"))
    if shape == "plan":
        return VIEW_PLAN % (row.get("planId"),)
    return VIEW_EFORMS % (row.get("procedureId"), row.get("noticeId"))


def texts(row, prefix):
    """Every language's spelling of one field, concatenated -- never the first non-empty one.

    THIS IS A RECALL DECISION AND IT IS MEASURED. 276 of 5 174 rows over three months carry no
    `titleFi` at all -- 5.3% -- and on some of them the Swedish or the English title is the only
    one there is. A reader that takes `titleFi or titleSv or titleEn` finds the same text most of
    the time and goes blind on exactly the rows where the languages disagree, which is where a
    bilingual municipality publishes to both audiences and puts different words in each.
    """
    parts = [row.get(prefix + suffix) for suffix in ("Fi", "Sv", "En", "Other")]
    return " ".join(p.strip() for p in parts if p and p.strip())


def cpv_codes(row):
    """The CPV codes, as a list. The index carries them space-separated in one string."""
    raw = row.get("cpvCodes")
    if isinstance(raw, (list, tuple)):
        codes = [str(c).strip() for c in raw]
    else:
        codes = str(raw or "").split()
    return [c for c in codes if c]


def value_of(row):
    """The estimated value, and `None` when the index is saying it does not know.

    `estimatedValue: 0.0` WITH A NULL CURRENCY IS NOT ZERO. Five rows in a 376-row corpus carry
    it and 258 carry a plain null; read literally, the board grows a column of 0-euro tenders and
    a reader learns to distrust the column rather than the rows. A real zero-valued procurement
    with a stated currency is left alone, because that is a buyer saying something.
    """
    amount, currency = row.get("estimatedValue"), row.get("currency")
    if amount is None:
        return None
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return None
    if not amount and not currency:
        return None
    return {"amount": amount, "currency": currency}


def documents_available(row):
    """`hilma`, `external` or `none` -- and it is carried all the way to the card.

    THE RISK ON THIS PORTAL IS NOT FAILURE, IT IS CONFIDENT SUCCESS. Only 11% of notices keep
    their documents on Hilma's own CDN, where they download anonymously; 89% sit on Cloudia and
    the other buyer platforms behind a supplier account, and some have no documents at all. The
    metadata is public in every case, so every one of them still produces a card.

    A card judged on a 964-character description and a card judged on a specification are
    different claims. Nothing downstream can tell them apart unless this flag travels with the
    tender, and neither of the other two country tools has ever had a tender it could not read
    but still had to publish.
    """
    url = (row.get("procurementDocumentsUrl") or "").strip()
    if not url:
        return "none"
    host = urllib.parse.urlparse(url).netloc.casefold()
    if host.endswith("hankintailmoitukset.fi"):
        return "hilma"
    return "external"


def documents_host(row):
    """WHICH portal holds them, because `external` does not tell a person where to go.

    Measured over 11 815 contract notices published 1 Sep 2025 - 31 Aug 2026: 79.6% of every
    Finnish tender's documents sit on ONE host, `tarjouspalvelu.fi` (Cloudia, now Mercell), and
    10.1% on Hilma's own CDN. The remaining 10% are spread over 153 other hosts, none above 2.1%.
    So this is not Poland's 143-hosts-in-a-day problem: one supplier account moves a reader from
    10% of the country's documents to 90%.

    The host is carried on the card because the card is read by a person who then has to open
    something, and `external` sends them nowhere. It is also the number that decides whether an
    integration is ever worth building: at 11 815 notices a year that host is an engineering
    project, and at the four a month that survive the scope gate it is four clicks.
    """
    url = (row.get("procurementDocumentsUrl") or "").strip()
    if not url:
        return None
    host = urllib.parse.urlparse(url).netloc.casefold()
    return host[4:] if host.startswith("www.") else host


def notice(row, detail=None, kind=None):
    """One tender's facts, in the shape every country tool in this family produces.

    `row` is the index row and is the source of almost everything: this index carries eighty-six
    fields at list level, including the buyer's own description, so the facts a card needs are
    complete before any second request is made. `detail` is the notice document and adds only
    what the index does not state -- which today is nothing a card reads, and is passed anyway so
    that the seam matches the other countries' and so a later field has somewhere to arrive.

    The gate reads `title`, `titles`, `description` and `cpv_name` and nothing else, and it reads
    them before a single request is made for this tender -- which is what makes Finland's whole
    window gateable for the cost of the one request that discovered it.
    """
    codes = cpv_codes(row)
    detail = detail or {}
    return {
        "pid": home_id(row),
        "procedure_id": row.get("procedureId") or None,
        "notice_id": row.get("noticeId") or None,
        # The human key, and the one a card is keyed on. Null on plans, which have no notice.
        "ref": row.get("noticeNumber") or str(row.get("id") or ""),
        "row_id": row.get("id"),
        "shape": shape_of(row),
        "kind": kind or kind_of(row),
        "main_type": row.get("mainType"),
        "type": row.get("type"),
        # The display title: Finnish when there is one, and whatever there is when there is not.
        "title": (row.get("titleFi") or row.get("titleSv") or row.get("titleEn")
                  or row.get("titleOther")),
        # What the gate actually reads. Four languages, concatenated. See `texts`.
        "titles": texts(row, "title"),
        "description": texts(row, "description"),
        "buyer": (row.get("organisationNameFi") or row.get("organisationNameSv")
                  or row.get("organisationNameEn")),
        "buyer_id": row.get("organisationNationalRegistrationNumber"),
        "buyer_type": row.get("organisationType"),
        "published": row.get("datePublished"),
        "deadline": row.get("deadline") or row.get("expirationDate"),
        "expires": row.get("expirationDate"),
        "cpv": codes,
        "cpv_main": codes[0] if codes else None,
        # Named for the field `policy.py` reads as one of its text surfaces. Hilma does not serve
        # a classification NAME, only codes, so this stays empty and the gate leans on the
        # description -- which this index, unlike Estonia's, does carry.
        "cpv_name": None,
        "nuts": row.get("nutsCodes"),
        "procedure": row.get("procedureType"),
        "value": value_of(row),
        "framework": bool(row.get("includesFrameworkAgreement")),
        # Spelled exactly as the API spells it, typo and all. Correcting it here would read a
        # field that does not exist and report every dynamic purchasing system as not being one.
        "dps": bool(row.get("includesDynamicPurcharingSystem")),
        "eu": bool(row.get("isEuProcurement")),
        "national": bool(row.get("isNationalProcurement")),
        "corrigendum": bool(row.get("isCorrigendum")),
        "cancelled": bool(row.get("isCancelled")),
        "supersedes": row.get("previousNoticeNumber") or [],
        "ted_id": row.get("tedPublicationId"),
        "documents_url": row.get("procurementDocumentsUrl"),
        "documents_available": documents_available(row),
        "documents_host": documents_host(row),
        "tendering_in_hilma": bool(row.get("tenderingInHilma")),
        "link": view(row),
        "record": row,
    }


def catalogue(detail):
    """Every document the portal lists for a notice, the ones it will serve and the rest apart.

    `status` is 10 on every attachment observed. A different value has never been seen and is
    therefore treated as not-yet-available rather than guessed at: downloading it would fetch an
    error page under a 200, and the manifest would carry it as a document.

    There is no per-file timestamp anywhere in this API -- nothing here corresponds to Estonia's
    `stampUpd`. What catches a replaced specification is the set of attachment GUIDs, compared
    between runs by the fingerprint.
    """
    public, withheld = [], []
    for entry in (detail or {}).get("attachments") or []:
        identity = entry.get("id")
        if not identity:
            continue
        row = {
            "id": identity,
            "name": entry.get("name") or identity,
            "status": entry.get("status"),
            "entity_type": entry.get("attachmentEntityType"),
            "ready": entry.get("status") == ATTACHMENT_READY,
            "download": ATTACHMENT % identity,
        }
        (public if row["ready"] else withheld).append(row)
    return public, withheld


def collect(row, kind=None):
    """The facts and the catalogue, in the one request that carries them.

    One, not three: this index states the tender at list level, so the only thing the notice
    document adds is the attachment list. A tender whose documents live on somebody else's
    platform costs no request at all here, and that is most of them.

    THE DETAIL CALL IS ALSO THE LINK CHECK. It is the one address on this portal that answers 404
    for something that is not there -- every rendered page answers 200 with the same 9 684-byte
    shell -- so asking it does two jobs. A notice number that comes back different from the one
    asked for is a link this run must not publish, and `link_verified` says so.
    """
    facts = notice(row, kind=kind)
    document, verified = None, None
    if facts["shape"] == "eforms" and facts.get("procedure_id") and facts.get("notice_id"):
        document = detail(facts["procedure_id"], facts["notice_id"])
        if document is None:
            raise Refused("the portal has no notice at procedure %s / notice %s"
                          % (facts["procedure_id"], facts["notice_id"]))
        verified = str(document.get("noticeNumber") or "") == str(facts.get("ref") or "")
    public, withheld = catalogue(document)
    facts.update({"link_verified": verified, "documents": public,
                  "withheld_documents": withheld, "detail": document})
    return facts


def main(argv=None):
    ap = argparse.ArgumentParser(description="One Hilma notice, as JSON.")
    ap.add_argument("reference", help="the notice number a person quotes, e.g. 2026-056114")
    args = ap.parse_args(argv)

    import fi_targets
    row = fi_targets.one(args.reference)
    if row is None:
        raise SystemExit("fi_page: the index served no row for %s" % args.reference)
    out = {k: v for k, v in collect(row["record"]).items()
           if k not in ("record", "detail")}
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return out


if __name__ == "__main__":
    main()
