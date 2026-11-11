"""Transport for Finland: one notice, and whatever of it can be read, into a home.

FINLAND IS THE FIRST COUNTRY IN THIS FAMILY WITH A TENDER IT CANNOT READ AND MUST STILL PUBLISH.
Measured over the 11 815 contract notices published 1 Sep 2025 - 31 Aug 2026: 10.1% keep their
documents on Hilma's own CDN, where they download anonymously and whole, and 0.3% publish no
document link at all. Everything else is on a buyer platform behind a supplier account. Every one
of them still produces a card, because the metadata is public in every case and the index carries
the buyer's own description at list level -- a median of 964 characters, which is a real basis for
a judgement even when it is the only basis there is.

AND THE SHUT 90% IS ALMOST ALL ONE DOOR, WHICH IS THE FACT THAT DECIDES WHAT TO BUILD.
`tarjouspalvelu.fi` -- Cloudia, now Mercell -- holds 79.6% of the country's tender documents on
its own; the next host holds 2.1% and the remaining 153 share under 10% between them. This is not
Poland, where a single day scattered across 143 hosts. One supplier account moves a reader from
10% of Finland's documents to 90%.

So that door is opened, under the company's supplier account, by `fi_cloudia`: for a tender
that passed the recall gate or that a person is watching, and whose documents are on that one
host, the portal's archive is downloaded and its files become the home's documents exactly as
Hilma's would. Such a tender is `documents_available: portal`, not `hilma` -- the flag says where
the specification came from, and `hilma` would hide how much of the reading this route carries.
A tender on any other host, or one the portal step could not read, stays `external` and is
judged on the notice, with `documents_host` telling a person which page to open.

The number to watch is how often the reading step still says it could not decide for want of a
document, and on which hosts.

WHICH IS WHY `documents_available` TRAVELS WITH THE TENDER. A card judged on a description and a
card judged on a specification are different claims, and nothing downstream can tell them apart
unless the flag is carried. The risk on this route is not a failure that shows; it is a
confident success that does not.

THE STATUS CODE PROVES NOTHING ON THIS PORTAL and the rule holds for files as well as pages: an
error page arrives with a 200 as readily as an archive does. So a download is checked by what
arrived -- a file that begins with an HTML document is not the document that was asked for, no
matter what the status line said.

    python3 fi_fetch.py 2026-056114 --out work/FI
"""
import argparse
import hashlib
import io
import json
import os
import sys
import time

import fi_index
import fi_page
import fi_ted
import net

try:
    import changes as changes_mod
except Exception:                          # the fingerprint is optional for a bare fetch
    changes_mod = None

# What an HTML error page begins with, in the two spellings a portal serves. A downloaded
# attachment that starts this way is the portal saying no with a 200.
HTML_HEADS = (b"<!DOCTYPE", b"<!doctype", b"<html", b"<HTML")

# Long enough for a specification archive -- nine megabytes was the largest proved -- and short
# enough that a stalled connection does not hold a night.
DOCUMENT_TIMEOUT = 300


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _safe(name):
    """A member name that cannot climb out of the home it is written into."""
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ord(ch) >= 32).strip().lstrip(".")
    return name or "unnamed"


def _write(home, name, payload):
    with open(os.path.join(home, name), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def download(entry, timeout=DOCUMENT_TIMEOUT):
    """One attachment's bytes, or a reason it has none.

    Returns `(data, reason)`. Exactly one of them is set. A reason is a fact about the file that
    belongs on the card; an exception would be a fact about the run, and the two must not be
    recorded the same way -- a document we could not reach is a gap, a document the portal will
    not serve is news about the tender.
    """
    if not entry.get("ready"):
        return None, ("the portal lists this attachment with status %s, which is not the one it "
                      "serves" % entry.get("status"))
    try:
        data, _ = net.get(entry["download"], headers={"User-Agent": fi_page.UA},
                          timeout=timeout, log=fi_page.log)
    except net.Unreachable as exc:
        return None, "could not be downloaded: %s" % str(exc)[:160]
    if not data:
        return None, "the CDN served an empty body"
    if data.lstrip()[:9] in HTML_HEADS or any(data.lstrip().startswith(h) for h in HTML_HEADS):
        return None, ("the CDN served an HTML page rather than the file, under a 200 -- %d bytes"
                      % len(data))
    return data, None


def store(home, attachments):
    """Every attachment the portal will serve, written under `originals/`.

    The name is the portal's own, made safe. Two attachments of one notice have never collided,
    but a numbered suffix costs nothing and a silently overwritten specification costs a tender.
    """
    originals = os.path.join(home, "originals")
    os.makedirs(originals, exist_ok=True)
    records, withheld, seen, total = [], [], set(), 0

    for entry in attachments:
        data, reason = download(entry)
        if data is None:
            withheld.append({"id": entry["id"], "title": entry["name"], "reason": reason,
                             "download": entry["download"], "files": []})
            continue
        filename = _safe(entry["name"])
        stem, dot, ext = filename.rpartition(".")
        n = 1
        while filename.casefold() in seen:
            n += 1
            filename = "%s (%d)%s%s" % (stem or filename, n, dot, ext)
        seen.add(filename.casefold())

        with open(os.path.join(originals, filename), "wb") as fh:
            fh.write(data)
        total += len(data)
        records.append({
            "id": entry["id"],
            "title": entry["name"],
            "type_code": entry.get("entity_type"),
            "subtype": None,
            "section": "current",
            # The index carries no per-file timestamp at all, so there is nothing honest to put
            # here. What catches a replaced specification is the set of attachment GUIDs, which
            # `changes` compares through the digest below.
            "publish_date": None,
            "catalogued": True,
            "member": None,
            "download": entry["download"],
            "files": [{"filename": filename,
                       # `path` is what the extractor opens, relative to the home.
                       "path": "originals/%s" % filename,
                       "original_name": entry["name"],
                       "sha256": _sha256(data),
                       "bytes": len(data)}],
        })
    return records, withheld, total


def extract(home):
    """Turn the originals into Markdown, then give each one its permanent address.

    `normalize.py` is the same code in every country tool and knows nothing about any of them --
    it reads `manifest.json` and writes `normalized/`. What it does not do is name the results
    the way a reader addresses them, so that happens here: `doc/<digest>.md`, where the digest is
    `changes.document_key` over the ORIGINAL bytes and the document's place. An unchanged
    document therefore keeps the same address for ever and costs nothing to re-deliver, and a
    superseded one stays readable instead of being overwritten.
    """
    try:
        import normalize
    except ImportError as exc:
        return {"documents": [], "unreadable_files": [],
                "skipped": "normalize unavailable: %s" % exc}

    out = os.path.join(home, "normalized")
    stdout = sys.stdout
    try:
        sys.stdout = io.StringIO()             # the extractor narrates; a fetch does not
        normalize.main(["--in", home, "--out", out])
    finally:
        sys.stdout = stdout

    with open(os.path.join(out, "manifest_normalized.json"), encoding="utf-8") as fh:
        normalized = json.load(fh)

    doc_dir = os.path.join(home, "doc")
    os.makedirs(doc_dir, exist_ok=True)
    for entry in normalized.get("documents", []):
        if entry.get("also_listed_under") or not entry.get("markdown_path"):
            continue
        source = os.path.join(out, entry["markdown_path"])
        if not os.path.exists(source):
            continue
        key = (changes_mod.document_key(entry.get("original_sha256"), entry.get("source"))
               if changes_mod else (entry.get("original_sha256") or "")[:16])
        with open(source, encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(doc_dir, "%s.md" % key), "w", encoding="utf-8") as fh:
            fh.write(text)
        entry["doc"] = "doc/%s.md" % key
    return normalized


def _with_text(documents, normalized):
    """Point each index entry at the Markdown its bytes produced."""
    by_sha = {}
    for entry in normalized.get("documents", []):
        if entry.get("doc"):
            by_sha.setdefault(entry.get("original_sha256"), []).append(entry)
    out = []
    for row in documents:
        texts = by_sha.get(row["sha256"], [])
        out.append(dict(row,
                        doc=texts[0]["doc"] if texts else None,
                        chars=sum(t.get("markdown_chars", 0) for t in texts) or None))
    return out


def fetch(row, out_root, with_text=True):
    """One notice into `<out_root>/tenders/<pid>/`, in the delivery shape.

    `index.json` is written LAST and `state.json` after it, exactly as every tool in this family
    promises: the index existing is a reader's proof that the home is whole, and a fingerprint
    that landed before the documents it vouches for would let the next run skip text that is not
    there.
    """
    pid = str(row["pid"])
    home = os.path.join(out_root, "tenders", pid)
    os.makedirs(home, exist_ok=True)

    # THE FACTS AND THE CATALOGUE IN ONE CALL, through the seam every country tool answers. It
    # also performs the link check: the detail address is the one thing on this portal that
    # answers 404 for something that is not there, so asking it does two jobs.
    if row.get("source") == "Tarjouspalvelu":
        # A tender only the portal's own list carries: Hilma has no notice to ask, so the row
        # is the whole of the facts and there is no link to verify against a detail API.
        facts = {k: v for k, v in row.items() if k != "record"}
        facts.update(link_verified=None, documents=[], withheld_documents=[], detail=None)
    else:
        facts = fi_page.collect(row.get("record") or {}, kind=row.get("kind"))
    document = facts.pop("detail", None)
    verified = facts.get("link_verified")
    attachments = facts.pop("documents", []) or []
    unready = facts.pop("withheld_documents", []) or []

    records, withheld, total = ([], [], 0)
    if attachments:
        records, withheld, total = store(home, attachments)

    # THE DOCUMENTS HILMA DOES NOT HOLD, FROM THE ONE PORTAL THAT HOLDS 80% OF THEM. Only reached
    # for a tender somebody already wants -- `fi_day` never fetches a gated notice -- and it
    # never fails a fetch: a tender the portal would not give stays `external`, judged on the
    # notice as before, with the reason on its index.
    available = row.get("documents_available")
    portal = None
    if available == "external" and not records:
        import fi_cloudia
        portal = fi_cloudia.fetch_into(home, row)
        if portal and portal["status"] == "read":
            records, total, available = portal["records"], portal["bytes"], "portal"
    # What a portal listed. For the archive that is what it held, so a portal-read tender does
    # not report eight documents out of none.
    listed = len(attachments) + len(unready) + (len(records) if available == "portal" else 0)
    # An attachment the portal lists but is not yet serving is withheld for a different reason
    # from one whose download failed, and both belong on the card.
    withheld = withheld + [{"id": u["id"], "title": u["name"], "download": u["download"],
                            "reason": "the portal lists this attachment with status %s, which "
                                      "is not the one it serves" % u.get("status"),
                            "files": []} for u in unready]

    # THE SECOND PUBLIC SOURCE, FOR THE HALF OF THE COUNTRY THE EU ALSO PUBLISHES. Free, no
    # account, and it carries what Hilma's 86 fields do not: the procedure, the award criteria
    # with their weights, and whether the final phase is an electronic auction. On the measured
    # tender it nearly doubled the description. It may never fail a fetch -- a tender is not lost
    # because somebody else's service was down.
    ted = None
    if row.get("ted_id"):
        try:
            ted = fi_ted.collect(row["ted_id"])
        except Exception as exc:
            ted = {"ted_id": row["ted_id"], "error": str(exc)[:160]}
    if ted:
        _write(home, "ted.json", ted)

    procurement = {k: v for k, v in facts.items() if k != "record"}
    procurement["attachments_listed"] = listed
    # The whole notice as the portal serves it, so a later question about a field this tool does
    # not read yet can be answered from the disk instead of from the portal again.
    if document is not None:
        _write(home, "notice.json", document)

    manifest = {"pid": pid, "procurement_id": row.get("procedure_id"),
                "reference": row.get("ref"),
                "archive": None, "archive_sha256": None, "archive_bytes": 0,
                "documents": records,
                # Listed rather than dropped. A document the portal named and would not serve is
                # still a fact about the tender, and its arrival is news of the same kind as a
                # document's. `changes` reads this key.
                "withheld_records": [dict(w, files=[]) for w in withheld]}
    _write(home, "procurement.json", procurement)
    _write(home, "manifest.json", manifest)

    index = {
        "schema": "index/1",
        "pid": pid,
        "ref": row.get("ref"),
        "country": "FI",
        "kind": row.get("kind"),
        "source": row.get("source") or "Hilma",
        "notice_type": row.get("notice_type"),
        "link": row.get("link"),
        "link_verified": verified,
        "documents_link": row.get("documents_url"),
        # THE FLAG THAT MAKES A CARD HONEST. `hilma` means the specification was read from
        # Hilma; `portal` that it was read from the supplier portal under our account; `external`
        # that it exists behind an account we did not get through; `none` that the buyer
        # published no documents at all. Without it a reader cannot tell a judgement from a guess.
        "documents_available": available,
        # What the portal step did, and why it did not read, when it did not.
        "portal": ({k: portal[k] for k in ("host", "status", "reason", "files", "bytes")}
                   if portal else None),
        # Which portal, not merely "somewhere else". A person reading the card has to open
        # something, and 79.6% of the time it is one host they can hold an account for.
        "documents_host": row.get("documents_host"),
        # What the EU's own copy of this notice adds. Absent on the national tier, which TED
        # never sees -- that is the same one-directional fact the coverage check rests on.
        "ted": ({k: ted.get(k) for k in ("ted_id", "procedure", "lots", "award_criteria",
                                         "electronic_auction", "document_access", "chars",
                                         "source")}
                if ted and not ted.get("error") else None),
        "title": row.get("title"),
        # THE DESCRIPTION IS EVIDENCE HERE, NOT DECORATION, and that is why it is in the index
        # and not only in `procurement.json`. Eighty-nine per cent of Finnish tenders keep
        # their specification behind a supplier account, so for most cards the buyer's own
        # description -- a median of 964 characters, carried free in the search row -- is the
        # whole of what a judgement can be made on. An index that omitted it would send a
        # reader to a card with a title and a CPV code and nothing to weigh.
        "description": row.get("description"),
        "titles": row.get("titles"),
        "buyer": row.get("buyer"),
        "buyer_id": row.get("buyer_id"),
        "published": row.get("published"),
        "deadline": row.get("deadline"),
        "status": row.get("main_type"),
        "status_text": row.get("kind"),
        "procedure": row.get("procedure"),
        "procedure_text": row.get("type"),
        "value": row.get("value"),
        "cpv_main": row.get("cpv_main"),
        "cpv": row.get("cpv"),
        "framework": row.get("framework"),
        "dps": row.get("dps"),
        "eu": row.get("eu"),
        "national": row.get("national"),
        "supersedes": row.get("supersedes"),
        "ted_id": row.get("ted_id"),
        # What the portal listed and what it served, kept apart on purpose: a gap between them is
        # a fact about the delivery, not a rounding error.
        "catalogued_documents": listed,
        "withheld_documents": len(withheld),
        "documents": [{
            "id": r["id"],
            "name": r["files"][0]["original_name"],
            "title": r["title"],
            "sha256": r["files"][0]["sha256"],
            "bytes": r["files"][0]["bytes"],
            "original": "originals/%s" % r["files"][0]["filename"],
            "type_code": r["type_code"],
            "changed_at": None,
            "catalogued": True,
            "download": r["download"],
        } for r in records],
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _write(home, "index.json", index)

    normalized = (extract(home) if (with_text and records)
                  else {"documents": [], "unreadable_files": []})
    if normalized.get("documents"):
        index["documents"] = _with_text(index["documents"], normalized)
        index["text_documents"] = sum(1 for e in normalized["documents"]
                                      if e.get("markdown_path"))
        index["chars"] = normalized.get("chars", 0)
        _write(home, "index.json", index)          # rewritten with the text it now names

    # THE MAP THE READING STEP OPENS INSTEAD OF THE FOLDER. Walked once, deterministically, so
    # the pack's hundreds of thousands of characters become a handful of located quotes against
    # the questions scope.md asks. It may not fail a fetch: a tender with no index is a tender
    # read the slow way, not a tender lost.
    evidence = None
    if records:
        try:
            evidence = fi_index.build(home)
            index["evidence"] = "evidence.json"
            index["evidence_coverage"] = evidence["coverage"]
            _write(home, "index.json", index)
        except Exception as exc:
            index["evidence_error"] = str(exc)[:160]
            _write(home, "index.json", index)

    state = None
    if changes_mod is not None:
        # STAMPED WITH THE VERSIONS THAT PRODUCED IT, AND WITH FINLAND'S PARSER. Left unstamped,
        # a fingerprint cannot tell a buyer's amendment from our own extractor being upgraded
        # under a tender that never moved.
        import country as country_mod
        state = changes_mod.fingerprint(
            pid, procurement, manifest, normalized,
            tool=changes_mod.pipeline_version(),
            parser=changes_mod.parser_version(files=country_mod.parser_files("FI")))
        _write(home, "state.json", state)

    return {"pid": pid, "ref": row.get("ref"), "kind": row.get("kind"), "home": home,
            "documents": len(records), "catalogued": listed,
            "withheld": len(withheld), "uncatalogued": 0,
            "documents_available": available,
            "link_verified": verified,
            "bytes": total, "index": index, "state": state,
            "title": row.get("title"), "buyer": row.get("buyer"),
            "deadline": row.get("deadline"), "value": row.get("value"),
            "cpv_main": row.get("cpv_main")}


def main(argv=None):
    ap = argparse.ArgumentParser(description="One Finnish notice into a home.")
    ap.add_argument("reference", help="the notice number a person quotes, e.g. 2026-056114")
    ap.add_argument("--out", default="work/FI")
    args = ap.parse_args(argv)

    import fi_targets
    row = fi_targets.one(args.reference)
    if row is None:
        raise SystemExit("fi_fetch: the index served no row for %s -- a corrigendum may have "
                         "superseded it, in which case the successor is found through its "
                         "procedureId" % args.reference)
    done = fetch(row, args.out)
    print("%s (%s): %d of %d document(s), %d withheld, %.1f MB, documents %s -> %s"
          % (done["ref"], done["pid"], done["documents"], done["catalogued"], done["withheld"],
             done["bytes"] / 1048576.0, done["documents_available"], done["home"]))
    return done


if __name__ == "__main__":
    main()
