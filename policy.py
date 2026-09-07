#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""The recall gate: which notices are worth fetching, before a single byte moves.

WHY IT IS ITS OWN FILE. This is the one piece of judgement that is not about a country. A
CPV code means the same thing wherever it is filed, and the terms a caller recalls on are
their business rather than a portal's — so every country tool in this family runs the
identical rule, from a copy of this file, and none of them has to import another's driver
to get it.

WHAT THIS KNOWS ABOUT THE CALLER'S INTEREST: NOTHING — the same rule deliver_graph.py keeps
about its destination. The terms arrive in the environment, so this file names no industry,
no trade and no target, and a reader of this repository learns the shape of the filter
without learning what anyone points it at.
"""

import json
import os
import re


# THE ONE FILTER ALLOWED BEFORE A DOCUMENT EXISTS.
#
# A title is kept when it contains one of the caller's recall roots. Roots are matched as
# substrings rather than as whole words because the language this runs against inflects
# heavily; precision belongs to the later document-reading step, not to a title.
#
# AND FINLAND NEEDS BOTH KINDS OF MATCH IN ONE LIST, WHICH IT ALREADY HAS. Finnish carries our
# signal in three- and four-letter section abbreviations -- LVIA, RAU, SAH, KVR, ST -- and a
# bare substring finds them inside innocent words: `lvi` matches `pilvisiirtyma` on 18 titles in
# four days, `lvia` matches the street `Talvialantie`, `huolto` matches `huoltonosturi`, a
# crane. No second matcher is needed for this. `fold` below turns every run of punctuation into
# a space and pads the whole string, so a term WRITTEN WITH SPACES AROUND IT -- ` lvia ` --
# matches only on a token boundary, while one written bare stays a substring. The two
# behaviours live in one list and the policy author chooses per term.
#
# Two guards matter, and both fail toward fetching:
#   * no text at all means no evidence, so the notice is fetched;
#   * a classification code never vetoes a matching title, because the code is assigned by
#     the buyer and an imperfect one must not silently drop a notice whose title matches.
#
# Exclusions win over recall, and exclusion by code prefix covers notices whose title is
# absent or unhelpful.
#
# WHAT THIS KNOWS ABOUT THE CALLER'S INTEREST: NOTHING — the same rule deliver_graph.py
# keeps about its destination. The terms arrive in the environment, so this file names no
# industry, no trade and no target, and a reader of this repository learns the shape of the
# filter without learning what anyone points it at. An absent or unreadable policy means
# fetch everything, which is the only safe direction for a filter that failed to load:
# fetching too much costs time, and dropping silently costs a tender.
POLICY_ENV = "FI_POLICY"

# THE NAMES THIS TOOL DOES NOT READ, CHECKED RATHER THAN IGNORED. Every country tool in
# this family names its policy after its own country, and a deployment is set up by copying
# the last one that worked. An environment carrying only a sibling's name would load no
# policy at all here, and no policy means fetch everything -- a whole day drawn from a state
# register, reported as success. The one failure shape nobody catches is an empty morning
# that reads exactly like a quiet one, so a foreign name stops the run instead.
FOREIGN_POLICY_ENVS = ("EIS_POLICY", "LT_POLICY", "EE_POLICY")


def load_policy(source=None):
    """The caller's recall policy, or None. None means no filter — fetch everything.

    `source` is JSON text, a path to a JSON file, or None to read `EE_POLICY` from the
    environment. Tests pass a fixture through it; production passes nothing and the
    environment answers, so no deployment's terms are ever committed here.
    """
    raw = source if source is not None else os.environ.get(POLICY_ENV)
    if source is None and not raw:
        stray = [name for name in FOREIGN_POLICY_ENVS if os.environ.get(name)]
        if stray:
            raise EnvironmentError(
                "%s is set but this tool reads %s. Rename it: honouring neither would fetch "
                "the whole day ungated and report success."
                % (", ".join(stray), POLICY_ENV))
    if not raw or not raw.strip():
        return None
    text = raw
    if not raw.lstrip().startswith("{"):              # not JSON, so treat it as a path
        try:
            with open(raw, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            return None
    try:
        policy = json.loads(text)
    except ValueError:
        return None                       # an unreadable policy must fail open, never drop all
    recall = tuple(t.casefold() for t in (policy.get("recall_title_terms") or ()))
    if not recall:
        return None                       # incomplete policy must fail open, never drop all
    return (recall,
            tuple(policy.get("hard_exclude_prefixes") or ()),
            tuple(t.casefold() for t in (policy.get("hard_exclude_title_terms") or ())),
            # CODES THAT SURVIVE THEIR OWN DIVISION. A purchase can carry a main code
            # inside an excluded division and nothing else — a buyer files it under the
            # service it is bought as rather than the thing it is — and 62% of live
            # procurements carry one code only. Without an override such a notice is
            # dropped before a byte moves, which is the one failure the exclusions are
            # least allowed to cause.
            tuple(policy.get("override_prefixes") or ()),
            # CODES THAT RECALL ON THEIR OWN, because a title is not always the better
            # signal. Recall was title-only, and a code could exclude or rescue from an
            # exclusion but never bring anything in — so a procurement whose title is vague
            # and whose code is exact was dropped before a byte moved. That shape is common:
            # a buyer writes three words and then classifies the purchase precisely, and the
            # gate could hear only the three words. Absent, this changes nothing.
            tuple(policy.get("recall_cpv_prefixes") or ()))

# THE TEXTS THE GATE READS, AND WHY THERE ARE FOUR OF THEM HERE.
#
# A title is the obvious surface and in most countries it is the only one. This index publishes
# three more for free, and each of them is here because leaving it out was measured to lose
# notices.
#
#   `title`        the display title -- Finnish where there is one.
#   `titles`       ALL FOUR LANGUAGES CONCATENATED, never the first non-empty one. 276 of 5 174
#                  rows over three months carry no `titleFi` at all, and on some of them the
#                  Swedish or English title is the only one there is. A bilingual municipality
#                  writes different words in each, so the languages have to be added, not
#                  chosen between. `fi_page.texts` builds it.
#   `description`  the buyer's own description, in every language, at a median of 964
#                  characters. This index carries it in the SEARCH ROW, which is what lets the
#                  gate decide before a single request is made for the tender.
#   `cpv_name`     empty in Finland -- the index serves classification codes and no names --
#                  and kept so the surface list stays the same shape in every country tool.
TEXT_SURFACES = ("title", "titles", "description", "cpv_name")


# EVERY RUN OF PUNCTUATION BECOMES ONE SPACE, AND THE WHOLE STRING IS PADDED WITH ONE.
#
# This is what makes a SHORT term safe to write. A recall list for a language full of
# abbreviations needs entries like ` ats `, ` kv ` and ` vk `, and each of them is written
# with spaces around it precisely so it cannot match inside a longer word. That only works if
# the text is spaced the same way: without this, `KV,` and `(VK)` never match because of the
# punctuation, and a term at the very start or end of a title never matches because there is
# no space beyond it. Both failures are silent and both drop tenders.
#
# `[\W_]+` under Unicode keeps every letter and digit, Estonian ones included, and collapses
# everything else. It is deliberately not a named character class: a hand-written one has to
# list the language's letters, and the letter somebody forgets is the one that splits a word
# in half and stops a term matching it.
_SEPARATORS = re.compile(r"[\W_]+", re.UNICODE)


def fold(text):
    """One spelling of a text, so that a term written with spaces around it behaves.

    EMPTY STAYS EMPTY. Padding a blank string would make it two spaces and therefore truthy,
    and the caller reads emptiness as "no evidence, so fetch it". A gate that answered "this
    text contains none of your terms" for a notice with no text would fail CLOSED — the one
    direction this file is not allowed to fail in.
    """
    folded = _SEPARATORS.sub(" ", str(text or "")).casefold().strip()
    return " %s " % folded if folded else ""


def haystack(notice):
    """Everything the gate is allowed to read before a byte moves, folded for comparison."""
    parts = [str(notice.get(field) or "") for field in TEXT_SURFACES]
    return fold(" ".join(p for p in parts if p))


def cpv_codes(notice):
    """Every CPV code a notice carries, however the source spelled them."""
    codes = []
    raw = notice.get("cpv")
    if isinstance(raw, (list, tuple)):
        codes = [str(c.get("code", "")) if isinstance(c, dict) else str(c) for c in raw]
    elif raw:
        codes = [str(raw)]
    if notice.get("cpv_main"):
        codes.append(str(notice["cpv_main"]))
    return [c.strip() for c in codes if c and c.strip()]


def outside_scope(notice, policy):
    """Should this notice be excluded before any documents are fetched?"""
    if not policy:
        return False
    # Older policies carry three fields; the override list is the fourth and optional.
    recall_terms, exclude_prefixes, exclude_title_terms = policy[:3]
    override_prefixes = policy[3] if len(policy) > 3 else ()
    recall_prefixes = policy[4] if len(policy) > 4 else ()

    title = haystack(notice)
    if title and any(term in title for term in exclude_title_terms):
        return True

    codes = cpv_codes(notice)
    # An override is read anyway, wherever its division sits. The gate asks what the buyer
    # classified this as; whether the work is ours is a later and different question.
    overridden = bool(override_prefixes) and any(c.startswith(override_prefixes)
                                                 for c in codes)
    if (codes and exclude_prefixes and not overridden
            and all(c.startswith(exclude_prefixes) for c in codes)):
        return True

    # A CODE CAN RECALL, AND IT IS ASKED BEFORE THE TITLE. The exclusions above still
    # bind — an excluded title term or an all-excluded code set has already returned — so
    # this widens what is fetched and can never drop anything the old gate kept.
    if recall_prefixes and any(c.startswith(recall_prefixes) for c in codes):
        return False

    if not title:
        return False                      # missing signal fails open
    return not any(term in title for term in recall_terms)
