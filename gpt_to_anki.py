#!/usr/bin/env python3
"""
gpt_to_anki.py - turn ChatGPT answers into Anki cards, one card per section.

Typical use (watch mode):
    1. Run:  python gpt_to_anki.py --watch
    2. In ChatGPT, click the Copy button under a reply titled e.g.
           "Endocarditis – Definition"
    3. The script adds a card to the existing subdeck ...::Endocarditis
           Front: Definition?
           Back:  the copied reply (formatting kept)
    4. Copy the next reply ("Endocarditis – Aetiology", ...) and so on.

It also understands one big reply holding every section:
    # Endocarditis
    ## Definition ...
    ## Aetiology ...

Cards are sent straight into Anki through the AnkiConnect add-on
(https://ankiweb.net/shared/info/2055492159). Anki must be open.

Other examples:
    python gpt_to_anki.py --clipboard                  # add what's on the clipboard once
    python gpt_to_anki.py answer.md --dry-run          # preview, send nothing
    python gpt_to_anki.py answer.md --tsv cards.tsv    # file for File > Import instead
"""

import argparse
import html
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ===========================================================================
# YOUR SETTINGS - change these once, then you never need command-line options.
# ===========================================================================
NOTE_TYPE = "Syllabus Topic Prompt"   # Anki note type (Tools > Manage Note Types); falls back to "Basic"
FRONT_FIELD = "Front"
BACK_FIELD = "Back"
FRONT_TEMPLATE = "{section}?"          # e.g. "Definition?"  ({topic} = disease name)
INCLUDE_TITLE_ON_BACK = True           # keep "Endocarditis – Definition" as the back's heading
PARENT_DECK = ""                       # where NEW disease subdecks go, e.g. "Internal Medicine::Cardiology"
                                       # (existing subdecks like ...::Endocarditis are found automatically)
TAGS = ""                              # e.g. "chatgpt"

# One entry per card type: (text used on the card front, headings ChatGPT may use for it).
# Matching ignores case, "&" == "and", and US/UK spelling where listed.
SECTIONS = [
    ("Definition", [
        "definition", "definitions", "define", "overview",
    ]),
    ("Aetiology and classification", [
        "aetiology and classification", "etiology and classification",
        "classification and aetiology", "classification and etiology",
        "aetiology", "etiology", "aetiologies", "etiologies", "causes",
        "aetiology and risk factors", "etiology and risk factors",
        "causes and risk factors", "risk factors",
        "classification", "classifications", "types", "subtypes",
        "classification and causes", "causes and classification",
    ]),
    ("Pathogenesis", [
        "pathogenesis", "pathophysiology", "pathogenesis and pathophysiology",
        "pathophysiology and pathogenesis", "mechanism",
    ]),
    ("Physical examination and signs and symptoms", [
        "physical examination and signs and symptoms",
        "physical examination, signs and symptoms",
        "physical exam and signs and symptoms",
        "physical exam, signs and symptoms",
        "signs and symptoms and physical examination",
        "signs, symptoms and physical examination",
        "signs and symptoms and physical exam",
        "clinical features and physical examination",
        "signs and symptoms", "symptoms and signs", "signs", "symptoms",
        "physical examination", "physical exam", "physical examination findings",
        "physical exam findings", "examination findings", "clinical features",
        "clinical presentation", "clinical manifestations", "presentation",
    ]),
    ("Diagnosis", [
        "diagnosis", "diagnostics", "diagnostic workup", "diagnostic work-up",
        "workup", "work-up", "investigations", "diagnosis and workup",
        "diagnosis and investigations", "diagnostic criteria",
    ]),
    ("Management and treatment", [
        "management and treatment", "treatment and management", "management",
        "treatment", "therapy", "management and therapy",
    ]),
]

ANKI_CONNECT_URL = "http://127.0.0.1:8765"


# ===========================================================================
# Recognising headings
# ===========================================================================
def _norm(name):
    name = name.lower().replace("&", " and ")
    name = re.sub(r"[^a-z0-9,/ -]+", " ", name)
    name = name.replace("/", " and ")
    name = re.sub(r"\s*,\s*", ", ", name)
    name = re.sub(r"\s+", " ", name).strip(" ,-")
    return name


_ALIAS_TO_SECTION = {}
for _label, _aliases in SECTIONS:
    for _a in [_label] + _aliases:
        _ALIAS_TO_SECTION[_norm(_a)] = _label
        _ALIAS_TO_SECTION[_norm(_a).replace(",", "")] = _label

SECTION_ORDER = [label for label, _ in SECTIONS]
PLAIN_LEVEL = 7  # "level" of bold / numbered / "Name:" headings (deeper than any "#")

_LEAD_RE = re.compile(r"^(?P<hashes>#{1,6})?\s*(?P<marks>(?:[*_]{1,3}|\d+[.)]|[IVX]+[.)](?=\s))\s*)*")
_BULLET_LINE_RE = re.compile(r"^\s*(?:[-+•▪●]|\*(?!\*))\s")
_TOPIC_LINE_RE = re.compile(r"^\s*[*_]{0,3}\s*(?:topic|disease|condition)\s*[*_]{0,3}\s*[:\-]\s*[*_]{0,3}\s*(?P<t>.+?)\s*[*_]{0,3}\s*$", re.I)
_BOLD_ONLY_RE = re.compile(r"^\s*(?:\*\*|__)(?P<t>[^*_]+?)(?:\*\*|__)\s*:?\s*$")
_TITLE_SPLIT_RE = re.compile(r"\s+[–—-]\s+|\s*:\s+|\s*\|\s*")
_SECTION_OF_RE = re.compile(r"^(?P<s>.+?)\s+(?:of|in|for)\s+(?P<t>.+)$", re.I)


def _lookup(name):
    n = _norm(name)
    return _ALIAS_TO_SECTION.get(n) or _ALIAS_TO_SECTION.get(n.replace(",", ""))


def _clean(t):
    return re.sub(r"[*_`#]+", "", t).strip().strip(":").strip()


def _strip_lead(line):
    """Remove "#", bold markers and numbering. Returns (text, level, has_markup)."""
    s = line.strip()
    m = _LEAD_RE.match(s)
    level = len(m.group("hashes")) if m.group("hashes") else PLAIN_LEVEL
    return s[m.end():], level, bool(m.group(0).strip())


def classify(line):
    """
    Work out whether a line is a heading. Returns one of
        ("combined", level, topic, section, title)   "## Endocarditis – Definition"
        ("section",  level, section, inline_text, heading_text)
        ("topic",    level, topic)
    or None for ordinary content.
    """
    if not line.strip() or _BULLET_LINE_RE.match(line):
        return None

    m = _TOPIC_LINE_RE.match(line)
    if m:
        return ("topic", 0, _clean(m.group("t")))

    body, level, markup = _strip_lead(line)
    body = body.strip()
    is_md = level < PLAIN_LEVEL
    is_bold_only = bool(_BOLD_ONLY_RE.match(line))
    title = _clean(body)

    # "Endocarditis – Definition", "Definition – Endocarditis", "Definition of Endocarditis"
    if (is_md or is_bold_only) and len(title) <= 120:
        parts = [p.strip() for p in _TITLE_SPLIT_RE.split(title) if p.strip()]
        if len(parts) == 2:
            a, b = parts
            if _lookup(b) and not _lookup(a):
                return ("combined", level, a, _lookup(b), title)
            if _lookup(a) and not _lookup(b):
                return ("combined", level, b, _lookup(a), title)
            if not _lookup(a) and not _lookup(b) and not re.search(r"\d", b):
                # unknown section name, e.g. "Endocarditis – Complications"
                return ("combined", level, a, b[:1].upper() + b[1:], title)
        m = _SECTION_OF_RE.match(title)
        if m and _lookup(m.group("s")):
            return ("combined", level, _clean(m.group("t")), _lookup(m.group("s")), title)

    # "## Definition", "**Definition:** text", "1. Definition", "Definition: text"
    if ":" in body:
        name, rest = body.split(":", 1)
        markup = True
    else:
        name, rest = body, ""
    name = re.sub(r"\s*\([^)]*\)\s*", " ", name).strip(" *_")
    rest = rest.strip().strip("*_").strip()
    if name and len(name) <= 80 and _lookup(name) and (markup or not rest):
        return ("section", level, _lookup(name), rest, _clean(name))

    if is_md or is_bold_only:
        return ("topic", level, title)
    return None


# ===========================================================================
# Parsing a whole paste
# ===========================================================================
def parse(text, topic=None):
    """
    Returns {topic: {section: [chunk, ...]}} in order of appearance, where a chunk is
    {"heading": str, "title": str|None, "lines": [str]}.
    A section can have several chunks, e.g. separate "Signs and symptoms" and
    "Physical examination" headings that both belong on one card.
    """
    results = {}
    cur_topic = topic
    cur_chunk = None
    cur_level = None      # heading level of the open section; deeper headings are content
    titled = False        # open section came from a "Disease – Section" title
    preamble = []

    def open_chunk(t, section, heading, title=None):
        sections = results.setdefault(t, {})
        chunk = {"heading": heading, "title": title, "lines": []}
        sections.setdefault(section, []).append(chunk)
        return chunk

    for raw in text.splitlines():
        line = raw.rstrip()
        c = classify(line)
        kind = c[0] if c else None
        level = c[1] if c else None
        can_switch = cur_level is None or (level is not None and level <= cur_level)

        if kind == "combined" and c[3] not in SECTION_ORDER and cur_chunk is not None:
            kind = None  # "Duke criteria – Major" inside a reply is content, not a new card

        # A known "Disease – Section" title always starts a new card, so several copied
        # replies pasted one after another split correctly whatever their heading levels.
        if kind == "combined" and (can_switch or c[3] in SECTION_ORDER):
            _, level, t, section, title = c
            cur_topic = topic or t
            cur_chunk = open_chunk(cur_topic, section, section, title)
            cur_level, titled = level, True
            continue

        # Inside a titled reply, e.g. "Endocarditis – Aetiology and classification", any other
        # heading ("### Classification", "### Key points") is part of that card's answer.
        if titled and kind == "section" or titled and kind == "topic" and level != 0:
            kind = None

        if kind == "section" and can_switch:
            _, level, section, inline, heading = c
            cur_chunk = open_chunk(cur_topic, section, heading)
            if inline:
                cur_chunk["lines"].append(inline)
            cur_level, titled = level, False
            continue

        if kind == "topic":
            _, level, t = c
            starts_topic = (
                level == 0                                            # "Topic: X"
                or cur_level is None                                  # title before any section
                or (level < PLAIN_LEVEL and level < cur_level)        # "# X" above "## Section"
            )
            if starts_topic:
                if topic is None:
                    cur_topic = t
                cur_chunk, cur_level, titled = None, None, False
                continue

        if cur_chunk is not None:
            cur_chunk["lines"].append(line)
        elif line.strip():
            preamble.append(line.strip())

    # Sections with no title anywhere: use a short first line as the topic.
    if None in results:
        guess = _clean(preamble[0]) if preamble and len(preamble[0]) <= 80 else None
        results = {(guess if k is None else k): v for k, v in results.items()}
    return results


def _tidy(lines):
    lines = [l for l in lines if not re.match(r"^\s*([-*_]\s*){3,}$", l)]  # "---" rules
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    out = []
    for l in lines:
        if not l.strip() and out and not out[-1].strip():
            continue
        out.append(l)
    return out


# ===========================================================================
# Markdown -> Anki HTML
# ===========================================================================
_LIST_RE = re.compile(r"^(\s*)(?:[-*+•▪●]|(\d+)[.)])\s+(.*)$")


def _inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: "<b>%s</b>" % (m.group(1) or m.group(2)), s)
    s = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<i>\1</i>", s)
    s = re.sub(r"`(.+?)`", r"\1", s)
    return s


def _table(rows):
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    cells = [r for r in cells if not all(re.match(r"^:?-{2,}:?$", c) for c in r if c)]
    if not cells:
        return ""
    out = ["<table border=\"1\" style=\"border-collapse:collapse\">"]
    for i, r in enumerate(cells):
        tag = "th" if i == 0 and len(rows) > 1 and re.match(r"^\s*\|?\s*:?-{2,}", rows[1]) else "td"
        out.append("<tr>" + "".join("<%s>%s</%s>" % (tag, _inline(c), tag) for c in r) + "</tr>")
    out.append("</table>")
    return "".join(out)


def to_html(lines):
    out, stack, table = [], [], []  # stack of (indent, tag)

    def close_to(indent):
        while stack and stack[-1][0] >= indent:
            out.append("</%s>" % stack.pop()[1])

    def flush_table():
        if table:
            out.append(_table(table))
            del table[:]

    for line in lines:
        if line.strip().startswith("|"):
            close_to(-1)
            table.append(line)
            continue
        flush_table()
        m = _LIST_RE.match(line)
        if m:
            indent = len(m.group(1).expandtabs(4))
            tag = "ol" if m.group(2) else "ul"
            if stack and indent < stack[-1][0]:
                close_to(indent + 1)
            if stack and stack[-1][0] == indent and stack[-1][1] != tag:
                close_to(indent)
            if not stack or indent > stack[-1][0]:
                out.append("<%s>" % tag)
                stack.append((indent, tag))
            out.append("<li>%s</li>" % _inline(m.group(3)))
            continue
        close_to(-1)
        if not line.strip():
            out.append("<br>")
            continue
        md = re.match(r"^\s*#{1,6}\s+(.*)$", line)
        out.append(("<b>%s</b>" % _inline(md.group(1).strip("*_ ")) if md else _inline(line.strip())) + "<br>")
    close_to(-1)
    flush_table()
    result = "".join(out)
    result = re.sub(r"(<br>)+(?=<(?:ul|ol|table))", "", result)
    result = re.sub(r"(<br>){3,}", "<br><br>", result)
    return re.sub(r"(<br>)+$", "", result)


# ===========================================================================
# Cards
# ===========================================================================
def build_cards(text, topic=None, front_template=FRONT_TEMPLATE, title_on_back=INCLUDE_TITLE_ON_BACK,
                default_topic=None):
    """default_topic is used when the text names no disease, e.g. a reply titled just "Diagnosis"."""
    cards = []
    for t, sections in parse(text, topic).items():
        t = t or default_topic or "Untitled"
        ordered = sorted(sections, key=lambda s: SECTION_ORDER.index(s) if s in SECTION_ORDER else 99)
        for section in ordered:
            chunks = [dict(c, lines=_tidy(c["lines"])) for c in sections[section]]
            chunks = [c for c in chunks if c["lines"]]
            if not chunks:
                continue
            body = []
            for c in chunks:
                head = c["title"] if c["title"] and title_on_back else None
                if len(chunks) > 1 and not head:
                    head = c["heading"]
                if head:
                    body += ([""] if body else []) + ["### " + head]
                elif body:
                    body.append("")
                body += c["lines"]
            cards.append({
                "topic": t,
                "section": section,
                "front": front_template.format(topic=t, section=section),
                "back": to_html(body),
                "titled": any(c["title"] for c in chunks),
            })
    return cards


# ===========================================================================
# AnkiConnect
# ===========================================================================
class AnkiError(Exception):
    pass


def anki(action, url=ANKI_CONNECT_URL, **params):
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode()
    req = urllib.request.Request(url, payload, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.load(r)
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        raise AnkiError(
            "Can't reach Anki (%s).\n"
            "  -> Is Anki open? Is the AnkiConnect add-on (code 2055492159) installed?" % e
        )
    if resp.get("error"):
        raise AnkiError(resp["error"])
    return resp.get("result")


def _deck_key(name):
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def find_decks(topic, deck_names, parent=""):
    """Existing decks whose last part matches the topic, best matches first."""
    key = _deck_key(topic)
    leaf = {d: _deck_key(d.split("::")[-1]) for d in deck_names}
    exact = [d for d in deck_names if leaf[d] == key]
    if not exact:  # "Infective endocarditis" -> "...::Endocarditis" and the reverse
        exact = [d for d in deck_names if len(leaf[d]) >= 4 and
                 (re.search(r"\b%s\b" % re.escape(leaf[d]), key) or re.search(r"\b%s\b" % re.escape(key), leaf[d]))]
    if parent:
        under = [d for d in exact if d.lower().startswith(parent.lower() + "::")]
        exact = under or exact
    return sorted(exact, key=lambda d: (d.count("::") == 0, len(d)))


def _ask(prompt):
    if not sys.stdin or not sys.stdin.isatty():
        return ""
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


class DeckResolver:
    """Finds (or asks once for) the deck each topic's cards go into."""

    def __init__(self, fixed_deck, parent, url):
        self.fixed, self.parent, self.url = fixed_deck, parent, url
        self.cache = {}

    def __call__(self, topic):
        if self.fixed:
            return self.fixed
        if topic in self.cache:
            return self.cache[topic]
        names = anki("deckNames", url=self.url)
        matches = find_decks(topic, names, self.parent)
        if len(matches) == 1:
            deck = matches[0]
        elif len(matches) > 1:
            print("  Several decks match %r:" % topic)
            for i, d in enumerate(matches, 1):
                print("    %d) %s" % (i, d))
            choice = _ask("  Which one? [1] ")
            deck = matches[int(choice) - 1] if choice.isdigit() and 0 < int(choice) <= len(matches) else matches[0]
        elif self.parent:
            deck = "%s::%s" % (self.parent, topic)
            print("  New subdeck: %s" % deck)
        else:
            typed = _ask("  No deck called %r yet. Parent deck to create it in "
                         "(e.g. Internal Medicine::Cardiology), or Enter for top level: " % topic)
            deck = "%s::%s" % (typed, topic) if typed else topic
            print("  New deck: %s" % deck)
        self.cache[topic] = deck
        return deck


def _search_escape(s):
    return re.sub(r'([\\"*_:])', r"\\\1", s)


def pick_model(model, url=ANKI_CONNECT_URL):
    """Use the requested note type, or fall back to Basic if this Anki profile doesn't have it."""
    names = anki("modelNames", url=url)
    if model in names:
        return model
    if "Basic" in names:
        print("  Note type %r not found - using 'Basic'." % model)
        return "Basic"
    raise AnkiError("Note type %r not found (you have: %s). Change NOTE_TYPE at the top of the script."
                    % (model, ", ".join(names)))


def send_to_anki(cards, resolve_deck, model, front_field, back_field, tags,
                 allow_dupes=False, update=False, url=ANKI_CONNECT_URL):
    model = pick_model(model, url)
    fields = anki("modelFieldNames", url=url, modelName=model)
    for f in (front_field, back_field):
        if f not in fields:
            raise AnkiError("Note type %r has no field %r (it has: %s). Change NOTE_TYPE / FRONT_FIELD / "
                            "BACK_FIELD at the top of the script." % (model, f, ", ".join(fields)))
    added, updated, skipped = 0, 0, []
    made = set()
    for c in cards:
        deck = resolve_deck(c["topic"])
        if deck not in made:
            anki("createDeck", url=url, deck=deck)  # no-op if it exists
            made.add(deck)
        note = {
            "deckName": deck,
            "modelName": model,
            "fields": {front_field: c["front"], back_field: c["back"]},
            "tags": tags,
            "options": {"allowDuplicate": allow_dupes, "duplicateScope": "deck"},
        }
        try:
            anki("addNote", url=url, note=note)
            added += 1
            print("  + %-45s -> %s" % (c["front"], deck))
        except AnkiError as e:
            if "duplicate" in str(e).lower() and update:
                q = '"deck:%s" "%s:%s"' % (_search_escape(deck), front_field, _search_escape(c["front"]))
                ids = anki("findNotes", url=url, query=q)
                for nid in ids:
                    anki("updateNoteFields", url=url, note={"id": nid, "fields": {back_field: c["back"]}})
                if ids:
                    updated += 1
                    print("  ~ %-45s -> %s (updated)" % (c["front"], deck))
                    continue
            reason = "already in this deck - use --update to replace it" if "duplicate" in str(e).lower() else e
            skipped.append((c["front"], str(e)))
            print("  ! %-45s    skipped: %s" % (c["front"], reason))
    return added, updated, skipped


def write_tsv(cards, path, tags, deck):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("#separator:tab\n#html:true\n#notetype:%s\n#deck column:3\n#tags column:4\n" % NOTE_TYPE)
        for c in cards:
            d = deck or ("%s::%s" % (PARENT_DECK, c["topic"]) if PARENT_DECK else c["topic"])
            row = [c["front"], c["back"], d, " ".join(tags)]
            f.write("\t".join(x.replace("\t", " ").replace("\n", "<br>") for x in row) + "\n")


# ===========================================================================
# Clipboard
# ===========================================================================
_tk_root = None


def get_clipboard():
    global _tk_root
    try:
        import tkinter
        if _tk_root is None:
            _tk_root = tkinter.Tk()
            _tk_root.withdraw()
        _tk_root.update()
        return _tk_root.clipboard_get()
    except Exception:
        pass
    for cmd in (["pbpaste"], ["wl-paste", "-n"], ["xclip", "-selection", "clipboard", "-o"],
                ["xsel", "-b", "-o"], ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"]):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=True).stdout
        except Exception:
            continue
    raise RuntimeError("Couldn't read the clipboard (install tkinter, or xclip/wl-paste on Linux).")


# ===========================================================================
# CLI
# ===========================================================================
def looks_like_answer(text, cards, min_sections):
    """In watch mode, only react to copies that are clearly a section answer."""
    if any(c["titled"] for c in cards) or len(cards) >= min_sections:
        return True
    first = next((l for l in text.splitlines() if l.strip()), "")
    c = classify(first)
    return bool(cards) and c is not None and c[0] in ("section", "combined")


def preview(cards, resolve_deck=None):
    for c in cards:
        print("=" * 70)
        print("DECK: ", resolve_deck(c["topic"]) if resolve_deck else "(%s)" % c["topic"])
        print("FRONT:", c["front"])
        print("BACK: ", c["back"])
    print("=" * 70)
    print("%d card(s)" % len(cards))


def process(text, args, resolver, cards=None):
    if cards is None:
        cards = build_cards(text, args.topic, args.front, not args.no_title)
    if not cards:
        print("No sections found. Expected a title like 'Endocarditis – Definition' or headings like "
              "'Definition', 'Aetiology', 'Diagnosis'...")
        return 0
    if args.dry_run:
        preview(cards, resolver if args.deck else None)
        return len(cards)
    if not args.deck and any(c["topic"] == "Untitled" for c in cards):
        print("  Not added: can't tell which disease this is. Give it with --topic \"Endocarditis\".")
        return 0
    if args.tsv:
        write_tsv(cards, args.tsv, args.tags, args.deck)
        print("Wrote %d card(s) to %s - import it in Anki with File > Import." % (len(cards), args.tsv))
        return len(cards)
    added, updated, skipped = send_to_anki(cards, resolver, args.model, args.front_field, args.back_field,
                                           args.tags, args.allow_duplicates, args.update, args.url)
    parts = ["added %d" % added]
    if updated:
        parts.append("updated %d" % updated)
    if skipped:
        parts.append("skipped %d" % len(skipped))
    print("  Done: %s." % ", ".join(parts))
    return added + updated


def watch(args, resolver):
    print("Watching the clipboard - click Copy under a ChatGPT reply and its card goes into Anki.")
    print("Press Ctrl-C to stop.\n")
    last_topic = args.topic
    try:
        last = get_clipboard()  # ignore whatever was already copied
    except RuntimeError as e:
        sys.exit(str(e))
    while True:
        try:
            time.sleep(args.interval)
            text = get_clipboard()
            if not text or text == last:
                continue
            last = text
            cards = build_cards(text, args.topic, args.front, not args.no_title, default_topic=last_topic)
            if cards and looks_like_answer(text, cards, args.min_sections):
                if cards[-1]["topic"] != "Untitled":
                    last_topic = cards[-1]["topic"]
                print(time.strftime("[%H:%M:%S] ") + "Copied: %s" %
                      ", ".join("%s - %s" % (c["topic"], c["section"]) for c in cards))
                if any(c["topic"] == "Untitled" for c in cards):
                    print("  (no disease name in this reply - use --topic, or copy a titled reply first)")
                try:
                    process(text, args, resolver, cards)
                except AnkiError as e:
                    print("  Error: %s" % e)
                print()
        except KeyboardInterrupt:
            print("\nStopped.")
            return


def main(argv=None):
    p = argparse.ArgumentParser(description="Turn ChatGPT answers into Anki cards, one card per section.")
    p.add_argument("file", nargs="?", help="text/markdown file (default: paste into the terminal)")
    p.add_argument("-w", "--watch", action="store_true", help="keep watching the clipboard and add cards as you copy")
    p.add_argument("-c", "--clipboard", action="store_true", help="add what is on the clipboard now")
    p.add_argument("-d", "--deck", help="put every card in this exact deck (default: find the disease's subdeck)")
    p.add_argument("-p", "--parent", "--parent-deck", dest="parent_deck", default=PARENT_DECK,
                   help="where new disease subdecks are created, e.g. 'Internal Medicine::Cardiology'")
    p.add_argument("-t", "--topic", help="disease name (default: read from the reply's title)")
    p.add_argument("-n", "--dry-run", action="store_true", help="show the cards, send nothing")
    p.add_argument("--update", action="store_true", help="if a card with the same front exists in the deck, replace its back")
    p.add_argument("--allow-duplicates", action="store_true", help="add even if the same front already exists in the deck")
    p.add_argument("--tsv", metavar="PATH", help="write an Anki import file instead of using AnkiConnect")
    p.add_argument("--front", default=FRONT_TEMPLATE, help="front template using {section} and {topic} (default: %(default)s)")
    p.add_argument("--no-title", action="store_true", help="don't put the 'Disease – Section' title on the back")
    p.add_argument("--model", default=NOTE_TYPE, help="note type (default: %(default)s)")
    p.add_argument("--front-field", default=FRONT_FIELD)
    p.add_argument("--back-field", default=BACK_FIELD)
    p.add_argument("--tags", default=TAGS, help="space-separated tags")
    p.add_argument("--interval", type=float, default=0.7, help=argparse.SUPPRESS)
    p.add_argument("--min-sections", type=int, default=2, help=argparse.SUPPRESS)
    p.add_argument("--url", default=ANKI_CONNECT_URL, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    args.tags = args.tags.split()
    resolver = DeckResolver(args.deck, args.parent_deck.strip(": "), args.url)

    if args.watch:
        watch(args, resolver)
        return 0

    if args.file:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    elif args.clipboard:
        text = get_clipboard()
    else:
        if sys.stdin.isatty():
            eof = "Ctrl-Z then Enter" if sys.platform.startswith("win") else "Ctrl-D"
            print("Paste the ChatGPT answer, then press %s on an empty line:" % eof)
        text = sys.stdin.read()

    try:
        process(text, args, resolver)
    except AnkiError as e:
        print("Error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
