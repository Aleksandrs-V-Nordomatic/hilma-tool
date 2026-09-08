"""Index a tender's documents so the reading step opens only what answers a question.

WHAT THIS IS FOR. A Finnish building tender arrives as a pack: the Oulainen design tender is 28
documents and 301 900 characters, and one archive alone was 9 MB. Handing all of it to the
reading step costs a fortune and buys dilution -- the paragraph that decides the answer is one
sentence in the twenty-third file. This walks the pack once, deterministically, and produces a
map from the questions `scope.md` asks to the exact places in the exact files that speak to them.

DETERMINISTIC MEANS NO MODEL. Same bytes in, same index out, every time. Regular expressions and
character offsets, nothing that can have an opinion. Two runs over one pack produce byte-identical
evidence, which is what lets `changes` tell a buyer's amendment from our own tooling moving.

AND IT MAKES THE FAMILY'S HARD RULE MECHANICAL. `No located quote, no card`: every quote a card
carries must be findable in the file it came from, checked with a script before the card is
written. Here each hit is stored as file, character offset and the exact string, so checking it
is a slice rather than a search -- and a quote that has drifted is caught rather than trusted.

THE ONE FINNISH DOCUMENT THAT DECIDES MORE THAN THE OTHERS. `urakkarajaliite`, the
contract-boundary annex, states which contractor owns which work down to the fitting. That is the
component test -- does our work have its own boundary -- arriving as a document rather than as an
inference. `kit/EE/scope.md` asks the question; in Finland the buyer usually answers it in writing
and files it as an annex. It is ranked first for reading whenever it exists.

    python3 fi_index.py --home work/FI/tenders/35094
"""
import argparse
import io
import json
import os
import re
import sys
import time

SCHEMA = "evidence/1"
FILE = "evidence.json"

# How much text goes around a hit. Enough for a person to see the sentence and for the reading
# step to judge without opening the file, short enough that a pack of hits stays a page.
WINDOW = 260
# Per question, per document. A term that appears ninety times says the same thing ninety times.
PER_DOCUMENT = 3
PER_QUESTION = 24

# WHAT KIND OF DOCUMENT THIS IS, FROM ITS NAME. Finnish construction packs are named to a
# convention, and the name is a deterministic signal that costs nothing to read. Ordered: the
# first pattern that matches wins, so the specific ones come before `liite`.
KINDS = (
    ("urakkarajaliite", r"urakkaraja"),
    ("tarjouspyynto",   r"tarjouspyyn"),
    ("urakkaohjelma",   r"urakkaohjelma|urakkaohj"),
    ("tyoselostus",     r"ty[oö]selostus|selostus|selitys"),
    ("maaraluettelo",   r"m[aä][aä]r[aä]luettelo|m[aä][aä]r[aä]lask|luettelo|taulukko"),
    ("sopimus",         r"sopimus"),
    ("turvallisuus",    r"turvallisuus|riskiarvi"),
    ("aikataulu",       r"aikataulu"),
    ("piirustus",       r"piirustus|kaavio|pohja|leikkaus|asemapiirros|tasokuva"),
    ("espd",            r"espd"),
    ("liite",           r"liite|annex"),
)

# THE QUESTIONS, AND THE FINNISH THAT ANSWERS THEM. Each is a term the buyer writes, not a term
# we would; the point of the index is to find the buyer's own words. Written with the same
# boundary rule as the recall gate -- a term wrapped in spaces matches only on a token boundary,
# which is what keeps `lvi` out of `pilvisiirtyma`.
QUESTIONS = {
    # Is a control system being bought, and is it a building's?
    "control_system": (
        r"rakennusautomaati|kiinteist[oö]automaati|taloautomaati|automaatioj[aä]rjestelm"
        r"|ohjausj[aä]rjestelm|s[aä][aä]t[oö]j[aä]rjestelm|valvomo|alakeskus|s[aä][aä]t[oö]kaavio"
        r"|pistelist|IO-luettelo|kentt[aä]laite|\bBMS\b|BACnet|Modbus|KNX|\bDALI\b|LonWorks"),
    # Does our work have its own boundary -- the component test, in the buyer's words.
    "boundary": (
        r"urakkaraja|sivu-?urakka|aliurakka|jaettu urakka|kokonaisurakka|\bKVR\b|SR-urakka"
        r"|p[aä][aä]urakoitsij|hankintaraja|toimitusraja|urakkamuoto|urakkasuoritu"),
    # Could we be qualified for it at all -- the question no notice answers.
    "qualification": (
        r"soveltuvuus|referens|p[aä]tevyy|kelpoisuu|\bRALA\b|tilaajavastuu|Luotettava Kumppani"
        r"|liikevaiht|vastuuvakuutu|vakuutu|s[aä]hk[oö]t[oö]iden johtaja|\bS[123]\b|veronumero"
        r"|Tukes|kielivaatimu|suomen kielell"),
    # What is it worth, and when does it have to be done.
    "value": r"urakkasumma|kustannusarvio|arvonlis[aä]verot|hankinnan arvo|budjett|\bEUR\b|euroa",
    "schedule": r"aikataulu|valmistumi|ty[oö]n aloitu|takuuaika|luovutus|vastaanottotarkastu",
    # What the tender is actually for, when the automation is one section of something larger.
    "object": r"peruskorjau|uudisrakennu|saneeraus|laajennu|muutosty[oö]|purkuty[oö]",
}

# WHICH DOCUMENT TO OPEN FIRST. The boundary annex answers the question that refuses most Finnish
# tenders, so it leads; the invitation and the contract programme carry the commercial terms; the
# specification carries the scope. Drawings are last because an extracted drawing is a page of
# stray labels and almost never a sentence.
READING_ORDER = ("urakkarajaliite", "tarjouspyynto", "urakkaohjelma", "tyoselostus",
                 "maaraluettelo", "sopimus", "espd", "turvallisuus", "aikataulu", "liite",
                 "piirustus", "muu")


# WHAT THE DOCUMENT CALLS ITSELF, when its file name does not say. Finnish design packs are
# numbered rather than named -- the Lapua automation retrofit arrives as J6000.pdf through
# J6998.pdf -- so the name carries nothing and the heading carries everything. Read from the
# first few hundred characters, where a Finnish tender document states its own type in capitals.
HEADINGS = (
    ("urakkarajaliite", r"urakkaraja"),
    ("tarjouspyynto",   r"tarjouspyynt"),
    ("urakkaohjelma",   r"urakkaohjelma"),
    ("tyoselostus",     r"ty[oö]selostus|ty[oö]selitys|j[aä]rjestelm[aä]kuvaus|selostus"),
    ("maaraluettelo",   r"m[aä][aä]r[aä]luettelo|määrälask|laiteluettelo|pisteluettelo"),
    ("sopimus",         r"urakkasopimus|sopimusluonnos"),
    ("turvallisuus",    r"turvallisuusasiakirja"),
    ("aikataulu",       r"aikataulu"),
)
HEAD = 400


def kind_of(name, text=None):
    """What kind of document this is: from its name, and failing that from what it calls itself.

    Both are deterministic. The name is tried first because it is free and because a pack that
    names its files has usually named them honestly; the heading is the fallback for the packs
    that number them instead, which in Finland is most engineering design.
    """
    lowered = (name or "").casefold()
    for kind, pattern in KINDS:
        if re.search(pattern, lowered):
            return kind
    if text:
        head = _fold(text[:HEAD])
        for kind, pattern in HEADINGS:
            if re.search(pattern, head):
                return kind
    return "muu"


def _fold(text):
    """One spelling, so a term written with spaces around it matches on a token boundary.

    The same rule the recall gate uses, and for the same reason: Finnish carries our signal in
    three- and four-letter abbreviations, and a bare substring finds them inside innocent words.
    Offsets are preserved -- every character maps to exactly one -- so a hit found here can be
    sliced out of the original.
    """
    return re.sub(r"[\W_]", " ", text, flags=re.UNICODE).casefold()


# A TABLE OF CONTENTS ANSWERS NOTHING, AND IT ANSWERS FIRST. Every Finnish contract programme
# opens with one, so the earliest hits for `boundary` and `qualification` were landing on
# `2 URAKKAMUOTO ....................4` -- the right word, the wrong place, and the three hits a
# document is allowed were spent before the actual section was reached. Dot leaders are what a
# contents line is made of and no prose has them, so the test is a run of them near the hit.
LEADERS = re.compile(r"\.{5,}|\. \. \. \.|…{2,}")


def _line_at(text, offset):
    """The line a hit sits on. THE LINE, not the window around it, and that distinction is the
    whole test: a contents block sits immediately above the section it lists, so a 260-character
    window centred on the real section reaches back into the dots and a window-wide check throws
    the genuine hit away. A contents entry is one line; prose is not."""
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    return text[start:end if end != -1 else len(text)]


def _is_contents(line):
    """True when this line is an entry in a table of contents rather than a sentence."""
    if LEADERS.search(line):
        return True
    dots = line.count(".")
    # A page-number list without leaders: mostly digits and punctuation, hardly any words.
    return dots > 8 and dots > len(re.findall(r"[^\W\d_]{3,}", line, re.UNICODE))


def _quote(text, start, end):
    """The sentence around a hit, trimmed to whole words and to one line.

    THE WINDOW IS CUT AT THE CONTENTS, NOT MERELY CHECKED AGAINST IT. A section sits immediately
    below the contents block that lists it, so a window reaching back from a genuine hit picks up
    the dot leaders above and hands the card a quote that opens with forty full stops. The hit is
    right and the quote reads like a scrape, which is worse than useless on something a person is
    asked to trust.
    """
    left = max(0, start - WINDOW // 2)
    right = min(len(text), end + WINDOW // 2)
    before = text[left:start]
    cut = None
    for match in LEADERS.finditer(before):
        cut = match.end()
    if cut is not None:
        left += cut
    fragment = text[left:right].replace("\n", " ")
    return re.sub(r"\s+", " ", fragment).strip()


def scan(text, source, name):
    """Every question this document speaks to, with where it does so.

    Offsets are into the ORIGINAL text, not the folded one, so a caller can slice the file and
    get the same characters back. That is the whole point: the quote is checkable.
    """
    folded = _fold(text)
    found = {}
    for question, pattern in QUESTIONS.items():
        hits, skipped = [], 0
        for match in re.finditer(pattern, folded, re.I):
            if _is_contents(_line_at(text, match.start())):
                skipped += 1
                continue
            quote = _quote(text, match.start(), match.end())
            hits.append({
                "doc": source, "name": name,
                "offset": match.start(), "end": match.end(),
                "term": text[match.start():match.end()],
                "quote": quote,
            })
            if len(hits) >= PER_DOCUMENT:
                break
        # SAID RATHER THAN SWALLOWED. A document whose ONLY mentions are in its contents has a
        # heading for our subject and no section under it, and that is a fact about the tender.
        if skipped and not hits:
            hits = [{"doc": source, "name": name, "offset": None, "end": None, "term": None,
                     "quote": None,
                     "note": "named only in the table of contents (%d time(s)), never in a "
                             "section" % skipped}]
        if hits:
            found[question] = hits
    return found


def _read(path):
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def build(home):
    """Walk one tender's extracted text once and write `evidence.json` beside it.

    Reads what `normalize.py` produced -- `doc/<digest>.md`, whose name is the digest of the
    ORIGINAL bytes, so an unchanged document keeps its address and this index is stable across
    runs for everything the buyer did not touch.
    """
    doc_dir = os.path.join(home, "doc")

    # THE NAME THAT MEANS SOMETHING IS THE MEMBER'S, NOT THE ARCHIVE'S, and getting that wrong
    # made the first version of this file useless: every one of the Oulainen pack's 42 documents
    # inherited the name of the zip it came out of and classified as `tarjouspyynto`, while the
    # Lapua pack's 28 came back as digests and classified as `muu`. The normalized manifest holds
    # the real member path -- `.../Tarjouspyyntö_AKU__Oulaisten seurakuntakeskus.pdf` -- and the
    # markdown's own address is derived from the original bytes exactly as `fi_fetch` derives it.
    by_doc = {}
    normalized_path = os.path.join(home, "normalized", "manifest_normalized.json")
    if os.path.exists(normalized_path):
        try:
            import changes as changes_mod
        except Exception:
            changes_mod = None
        normalized = json.load(io.open(normalized_path, encoding="utf-8"))
        for entry in normalized.get("documents", []):
            if entry.get("also_listed_under") or not entry.get("markdown_path"):
                continue
            digest = (changes_mod.document_key(entry.get("original_sha256"), entry.get("source"))
                      if changes_mod else (entry.get("original_sha256") or "")[:16])
            source = (entry.get("source") or "").replace("\\", "/")
            by_doc["doc/%s.md" % digest] = source.split("/")[-1] or source

    documents, questions, chars = [], {}, 0
    if os.path.isdir(doc_dir):
        for filename in sorted(os.listdir(doc_dir)):
            if not filename.endswith(".md"):
                continue
            relative = "doc/%s" % filename
            text = _read(os.path.join(doc_dir, filename))
            name = by_doc.get(relative) or filename
            documents.append({"doc": relative, "name": name, "kind": kind_of(name, text),
                              "chars": len(text)})
            chars += len(text)
            for question, hits in scan(text, relative, name).items():
                questions.setdefault(question, []).extend(hits)

    for question in questions:
        # Whichever documents spoke first, capped -- an index nobody can read is not an index.
        questions[question] = questions[question][:PER_QUESTION]

    order = {kind: n for n, kind in enumerate(READING_ORDER)}
    reading = sorted(documents, key=lambda d: (order.get(d["kind"], 99), -d["chars"]))

    evidence = {
        "schema": SCHEMA,
        "home": os.path.basename(os.path.normpath(home)),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "documents": documents,
        # WHAT TO OPEN AND IN WHICH ORDER. The reading step follows this rather than the folder
        # listing, so the boundary annex is read before four megabytes of drawings.
        "reading_order": [{"doc": d["doc"], "kind": d["kind"], "name": d["name"],
                           "chars": d["chars"]} for d in reading],
        "questions": questions,
        "coverage": {
            "documents": len(documents),
            "chars": chars,
            "quotes": sum(len(v) for v in questions.values()),
            "questions_answered": sorted(questions),
            # NAMED, BECAUSE AN ABSENCE IS A FINDING. A pack with no `urakkarajaliite` and no
            # boundary words is a tender whose contract shape nobody wrote down, and the
            # component test then has nothing to stand on.
            "questions_silent": sorted(set(QUESTIONS) - set(questions)),
            "kinds": sorted({d["kind"] for d in documents}),
        },
    }
    with io.open(os.path.join(home, FILE), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(evidence, fh, ensure_ascii=False, indent=2)
    return evidence


def verify(home, evidence=None):
    """Every quote sliced back out of the file it claims to come from.

    `No located quote, no card` is the rule; this is the script that makes it a check rather
    than a promise. A quote that no longer slices out means the extractor changed under the
    index, which is exactly the thing that must not reach a card unnoticed.
    """
    evidence = evidence or json.load(io.open(os.path.join(home, FILE), encoding="utf-8"))
    checked = drifted = 0
    for hits in evidence.get("questions", {}).values():
        for hit in hits:
            if hit.get("offset") is None:
                # A note rather than a quote -- a document that names our subject only in its
                # contents. There is nothing to slice and nothing to have drifted.
                continue
            path = os.path.join(home, hit["doc"])
            if not os.path.exists(path):
                drifted += 1
                continue
            text = _read(path)
            checked += 1
            if text[hit["offset"]:hit["end"]] != hit["term"]:
                drifted += 1
    return {"checked": checked, "drifted": drifted, "ok": drifted == 0}


def summary(evidence):
    cover = evidence["coverage"]
    lines = ["%d document(s), %d chars, %d located quote(s) over %d question(s)"
             % (cover["documents"], cover["chars"], cover["quotes"],
                len(cover["questions_answered"]))]
    if cover["questions_silent"]:
        lines.append("  silent on: %s" % ", ".join(cover["questions_silent"]))
    for entry in evidence["reading_order"][:6]:
        lines.append("  read %-16s %7d chars  %s" % (entry["kind"], entry["chars"],
                                                     (entry["name"] or "")[:44]))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="Index one tender's documents.")
    ap.add_argument("--home", required=True)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    evidence = build(args.home)
    if args.json:
        json.dump(evidence, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    for line in summary(evidence):
        print(line)
    if args.verify:
        result = verify(args.home, evidence)
        print("quotes: %d checked, %d drifted -- %s"
              % (result["checked"], result["drifted"], "OK" if result["ok"] else "FAILED"))
        return 0 if result["ok"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
