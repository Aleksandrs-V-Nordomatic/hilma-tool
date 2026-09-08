"""The second public source: the same tender as the EU publishes it, free and without an account.

WHY THIS FILE EXISTS. Ninety per cent of Finnish tenders keep their documents on a platform that
answers nobody without an account, so most Finnish judgements are made on the notice alone. That
makes every free word of notice worth having -- and TED holds words Hilma does not.

Measured on `2026-054531`, the Imatra fire station automation side-contract: Hilma's search row
carries 2 436 characters of description, TED carries 4 735 of the same tender, and the extra 2 299
are not padding. They are the two facts that decide whether a tender is worth pursuing at all:

    the procedure   an open procedure whose final phase is an ELECTRONIC AUCTION under
                    hankintalaki 44 -- which changes what bidding means
    the award       price at 100% weight, with the scoring formula spelled out:
                    lowest comparison price / this bid's comparison price x 100

Neither appears anywhere in Hilma's 86 fields. A card that did not carry them would send somebody
to price-only reverse auction believing it was a quality competition.

WHO IS COVERED. Only the EU tier -- 52% of a measured Finnish day -- because that is what TED
publishes. The national tier never reaches it, which is also why a coverage check against TED is
one-directional (`fi_recon`). Every notice already carries its `tedPublicationId` when it has one,
so nothing has to be looked up to know whether this source applies.

WHAT IT DOES NOT GIVE, AND THIS WAS CHECKED RATHER THAN ASSUMED. The selection criteria are not
here. `TendererQualificationRequest` on the measured notice reads `epo-sub-espd` and `none`,
meaning the buyer asks for an ESPD and states the actual requirements in the tender documents --
which are the documents behind the account. So TED improves the picture and does not complete it.

    python3 fi_ted.py 556072-2026
"""
import argparse
import json
import re
import sys
import time
import urllib.error

import net

NOTICE = "https://ted.europa.eu/en/notice/%s/xml"
UA = "Mozilla/5.0 (compatible; eis-tool)"
# One request per EU tender, about twenty-three on a Finnish working day. Paced anyway, because
# this is somebody else's service and the run is not in a hurry.
PACE = 0.5
_last = [0.0]


class NotPublished(RuntimeError):
    """TED has no notice under this publication id.

    A fact about the tender rather than about the run: the national tier never reaches TED, and a
    number that looks like a publication id may belong to a notice that was withdrawn.
    """


def _pace():
    gap = PACE - (time.time() - _last[0])
    if gap > 0:
        time.sleep(gap)
    _last[0] = time.time()


def fetch(ted_id, timeout=60):
    """The notice as TED holds it, in XML. Raises `NotPublished` on a 404."""
    _pace()
    try:
        body, _ = net.get(NOTICE % str(ted_id).strip(), headers={"User-Agent": UA},
                          timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NotPublished("TED has no notice %s" % ted_id)
        raise
    return body.decode("utf-8", "replace")


def _blocks(xml, tag):
    return re.findall(r"<(?:\w+:)?%s\b[^>]*>.*?</(?:\w+:)?%s>" % (tag, tag), xml, re.S)


def _text(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def _values(xml, tag):
    """Every distinct non-trivial string under a tag, in document order.

    Deduplicated because TED repeats a lot -- an award criterion appears once on its own and
    again inside its lot -- and a card that showed the same paragraph three times would be read
    as carelessness rather than as thoroughness.
    """
    seen, out = set(), []
    for block in _blocks(xml, tag):
        value = _text(block)
        if len(value) > 3 and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def evidence(xml):
    """What this notice adds, as the small shape a card and a scope test read.

    Everything here is quoted from TED rather than summarised, because a card's quote has to be
    findable in its source: `no located quote, no card` is the rule this family runs on, and a
    paraphrase cannot be located.
    """
    descriptions = [d for d in _values(xml, "Description") if len(d) > 40]
    criteria = []
    for block in _blocks(xml, "AwardingCriterion"):
        weight = re.search(r"<(?:\w+:)?ParameterNumeric\b[^>]*>([^<]+)<", block)
        kind = re.search(r"<(?:\w+:)?AwardingCriterionTypeCode\b[^>]*>([^<]+)<", block)
        body = _text(block)
        criteria.append({"type": kind.group(1) if kind else None,
                         "weight": weight.group(1).strip() if weight else None,
                         "text": body[:1500]})
    procedure = re.search(r"<(?:\w+:)?ProcedureCode\b[^>]*>([^<]+)<", xml)
    # TED says in as many words whether the documents can be had. `restricted-document` is the
    # 90% case in Finland and is exactly what `documents_available` records from the other side.
    access = re.search(r"<(?:\w+:)?DocumentStatusCode\b[^>]*>([^<]+)<", xml) \
        or re.search(r">(restricted-document|public-document)<", xml)
    auction = bool(re.search(r"huutokau|auction", xml, re.I))

    return {
        "descriptions": descriptions,
        "chars": sum(len(d) for d in descriptions),
        "award_criteria": criteria,
        "procedure": procedure.group(1) if procedure else None,
        # A number a person reads as "how many ways is this contract split", which is the
        # question the component test asks of a Finnish building project.
        "lots": len(_blocks(xml, "ProcurementProjectLot")),
        "document_access": access.group(1) if access else None,
        "electronic_auction": auction,
        "qualification": _values(xml, "TendererQualificationRequest")[:4],
    }


def collect(ted_id):
    """The evidence for one TED publication id, or None when TED does not have it."""
    try:
        xml = fetch(ted_id)
    except NotPublished:
        return None
    found = evidence(xml)
    found["ted_id"] = str(ted_id)
    found["source"] = NOTICE % ted_id
    found["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return found


def summary(found):
    """One line for a run's log; the card gets the quotes themselves."""
    if not found:
        return "TED: not published"
    bits = ["%d chars over %d description(s)" % (found["chars"], len(found["descriptions"]))]
    if found.get("procedure"):
        bits.append("procedure %s" % found["procedure"])
    if found.get("lots"):
        bits.append("%d lot(s)" % found["lots"])
    for criterion in found.get("award_criteria") or []:
        if criterion.get("weight"):
            bits.append("%s at %s" % (criterion.get("type") or "criterion", criterion["weight"]))
    if found.get("electronic_auction"):
        bits.append("ELECTRONIC AUCTION")
    if found.get("document_access"):
        bits.append("documents %s" % found["document_access"])
    return "TED %s: %s" % (found["ted_id"], "; ".join(bits))


def main(argv=None):
    ap = argparse.ArgumentParser(description="One tender as the EU publishes it.")
    ap.add_argument("ted_id", help="the TED publication number, e.g. 556072-2026")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    found = collect(args.ted_id)
    if found is None:
        print("TED has no notice %s" % args.ted_id, file=sys.stderr)
        return 1
    if args.json:
        json.dump(found, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    print(summary(found))
    for description in found["descriptions"]:
        print("  - %s" % description[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
