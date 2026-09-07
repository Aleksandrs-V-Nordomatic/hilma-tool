#!/usr/bin/env python3
"""Say what the recall policy holds, and stop the night if it holds nothing.

    python3 tools/describe_policy.py policy.json

`policy.load_policy` fails open on purpose: an unreadable policy returns None rather than
dropping every notice, which is the right call inside a library. For an unattended night it is
the wrong outcome -- no gate means fetching every attachment the window holds because a secret
was misspelt -- so the night asks this question separately, before the portal is touched.

A SCRIPT AND NOT SIX LINES OF PYTHON INSIDE THE WORKFLOW. The inline version of this repository's
first probe never ran at all: its Python sat at column zero and ended the YAML block scalar, so
GitHub could not parse the workflow and silently offered no dispatch trigger. Anything longer
than a command belongs in a file that can be run on a laptop and covered by a test.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import policy

# Read BY POSITION, never unpacked. A fixed-width unpack breaks the night the day the policy
# grows a field, and this script exists to protect the night.
NAMES = ("recall term", "excluded code prefix", "excluded title term", "override",
         "recall code")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    path = argv[0] if argv else "policy.json"

    rules = policy.load_policy(path)
    if rules is None:
        # WHICH OF THE TWO, because they are different mistakes wearing one symptom. A policy
        # that does not parse arrived broken; one that parses and carries no rule arrived empty,
        # truncated, or renamed around. Both fetch the whole window ungated, and the person
        # fixing it at 04:00 should not have to guess which.
        try:
            with open(path, encoding="utf-8") as fh:
                json.load(fh)
        except (OSError, ValueError) as bad:
            print("the recall policy did not parse (%s) -- refusing to fetch the whole window "
                  "ungated" % bad, file=sys.stderr)
            return 1
        print("the recall policy parsed but carries no rule -- refusing to fetch the whole "
              "window ungated", file=sys.stderr)
        return 1

    print("policy: " + ", ".join(
        "%d %s(s)" % (len(group), NAMES[i] if i < len(NAMES) else "field %d" % (i + 1))
        for i, group in enumerate(rules)))

    # A FINNISH POLICY WITH NO SPACED TERM IS PROBABLY A MISTAKE, AND SAYING SO IS FREE.
    # Finland's signal is carried by three- and four-letter section abbreviations, and as bare
    # substrings they match inside ordinary words: `lvi` brings 791 rows out of 5 174 where
    # ` lvi ` brings 70, and the extra 721 are things like `pilvisiirtyma`. A policy written by
    # copying another country's -- which is how every one of these is written -- will have none.
    recall = rules[0] if rules else ()
    if recall and not any(term.startswith(" ") and term.endswith(" ") for term in recall):
        print("::warning::no recall term is written with spaces around it. In Finnish that "
              "means every abbreviation is matched as a bare substring, which is how `lvi` "
              "comes to match `pilvisiirtyma`. See fi_policy.example.json.")

    # A WIDE EXCLUSION IS ABSOLUTE AND SILENT, and it is the one policy mistake this gate
    # cannot fail loudly on. `hard_exclude_prefixes` is tested BEFORE the recall terms, so a
    # notice whose every code sits inside an excluded prefix is dropped however well its text
    # matches -- which is correct, and is why the prefixes exist, and is also why a prefix that
    # names a whole division deletes a fifth of a day without a word. Measured on 5 925 Latvian
    # notices: excluding division 45 would have dropped 591 of the 2 147 the gate kept, 71 would
    # have dropped 420, 50 would have dropped 204. Both live policies write theirs four to six
    # digits long.
    wide = sorted(p for p in (rules[1] if len(rules) > 1 else ()) if len(str(p)) < 4)
    if wide:
        print("::warning::%s exclude(s) a whole CPV division rather than a purchase: %s. An "
              "exclusion beats a matching title, so this drops every notice carrying only "
              "codes in it -- silently. Lengthen the prefix, or add an override_prefixes entry."
              % (len(wide), ", ".join(wide)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
