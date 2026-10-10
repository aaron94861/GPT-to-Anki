#!/usr/bin/env python3
"""
gpt_to_anki.py - turn a ChatGPT answer into Anki cards, one card per section.

A ChatGPT answer like:

    # Acute Pancreatitis
    ## Definition
    Acute inflammation of the pancreas ...
    ## Etiology
    - Gallstones
    - Alcohol
    ...

becomes Basic cards in your chosen deck:

    Front: Acute Pancreatitis - Definition     Back: Acute inflammation of ...
    Front: Acute Pancreatitis - Etiology       Back: * Gallstones * Alcohol
    ...

Cards are sent straight into Anki through the AnkiConnect add-on
(https://ankiweb.net/shared/info/2055492159). Anki must be open.

Usage examples:
    python gpt_to_anki.py --deck "Medicine::IM"            # paste, then Ctrl-D / Ctrl-Z+Enter
    python gpt_to_anki.py --deck "Medicine::IM" --clipboard
    python gpt_to_anki.py --deck "Medicine::IM" --watch    # copy in ChatGPT -> cards appear
    python gpt_to_anki.py notes.md --dry-run               # preview, send nothing
    python gpt_to_anki.py notes.md --tsv cards.tsv         # file for File > Import instead
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

# ---------------------------------------------------------------------------
# Sections: each entry is one card. Edit names / aliases / order freely.
# The first value is the label printed on the card front; the aliases are the
# headings ChatGPT might use for it (case-insensitive, "&" == "and").
# Several headings may map to one card - their text is merged under subheadings.
# ---------------------------------------------------------------------------
SECTIONS = [
    ("Definition", [
        "definition", "definitions", "define", "overview", "what is it",
    ]),
    ("Etiology", [
        "etiology", "aetiology", "etiology and risk factors", "causes",
        "causes and risk factors", "risk factors", "etiologies",
    ]),
    ("Pathogenesis", [
        "pathogenesis", "pathophysiology", "pathogenesis and pathophysiology",
        "pathophysiology and pathogenesis", "mechanism",
    ]),
    ("Physical Examination & Signs and Symptoms", [
        "physical examination and signs and symptoms",
        "physical examination, signs and symptoms",
        "physical exam and signs and symptoms",
        "physical exam, signs and symptoms",
        "signs and symptoms and physical examination",
        "signs, symptoms and physical examination",
        "signs and symptoms", "symptoms and signs", "signs", "symptoms",
        "physical examination", "physical exam", "physical examination findings",
        "physical exam findings", "exam findings", "clinical features",
        "clinical presentation", "presentation", "history and physical",
    ]),
    ("Diagnosis", [
        "diagnosis", "diagnostics", "diagnostic workup", "diagnostic work-up",
        "workup", "work-up", "investigations", "diagnosis and workup",
        "diagnosis and investigations", "labs and imaging",
    ]),
    ("Management & Treatment", [
        "management and treatment", "treatment and management", "management",
        "treatment", "therapy", "management and therapy",
    ]),
]

DEFAULT_DECK = "Default"
DEFAULT_MODEL = "Basic"
DEFAULT_FRONT_TEMPLATE = "{topic} - {section}"
ANKI_CONNECT_URL = "http://127.0.0.1:8765"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
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

_LEAD_RE = re.compile(r"^(?P<hashes>#{1,6}\s*)?(?P<marks>(?:[*_]{1,3}|\d+[.)]|[IVX]+[.)](?=\s))\s*)*")


def _split_heading(line):
    """
    Split a possible heading line into (name, inline_text, structural).
    Handles "## Definition", "**Definition:** text", "1. Definition", "Definition: text".
    structural is True when the line carries heading markup (#, bold, numbering, colon).
    """
    s = line.strip()
    lead = _LEAD_RE.match(s)
    structural = bool(lead.group(0))
    s = s[lead.end():]
    if ":" in s:
        name, rest = s.split(":", 1)
        structural = True
    else:
        name, rest = s, ""
    name = re.sub(r"\s*\([^)]*\)\s*", " ", name).strip(" *_")
    rest = rest.strip().strip("*_").strip()
    return name, rest, structural


_TOPIC_RE = re.compile(r"^\s*(?:[*_]{0,3})\s*(?:topic|disease|condition)\s*[:\-]\s*(?P<t>.+?)\s*(?:[*_]{0,3})\s*$", re.I)
_MD_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(?P<t>.+?)\s*#*\s*$")
_BOLD_LINE_RE = re.compile(r"^\s*(?:\*\*|__)(?P<t>[^*_]+?)(?:\*\*|__)\s*:?\s*$")


_BULLET_LINE_RE = re.compile(r"^\s*[-+*\u2022\u25aa\u25cf]\s")


def match_section(line):
    """Return (section_label, inline_text) if the line is a section heading, else None."""
    if _BULLET_LINE_RE.match(line):  # "- Treatment: antibiotics" is content, not a heading
        return None
    name, rest, structural = _split_heading(line)
    if not name or len(name) > 80:
        return None
    n = _norm(name)
    label = _ALIAS_TO_SECTION.get(n) or _ALIAS_TO_SECTION.get(n.replace(",", ""))
    if not label:
        return None
    if rest and not structural:
        return None
    return label, rest


def match_topic(line):
    """Return a topic name if the line looks like a non-section title, else None."""
    m = _TOPIC_RE.match(line)
    if m:
        return _clean_title(m.group("t"))
    for rx in (_MD_HEADING_RE, _BOLD_LINE_RE):
        m = rx.match(line)
        if m and not match_section(line):
            return _clean_title(m.group("t"))
    return None


def _clean_title(t):
    t = re.sub(r"[*_`]+", "", t).strip().rstrip(":").strip()
    return t


def parse(text, topic=None):
    """
    Split text into topics and sections.
    Returns a list of (topic, {section_label: [(heading, [lines]), ...]}) in order of appearance.
    A new markdown/bold title (or "Topic: X") that is not a section starts a new topic,
    so one paste can hold several diseases.
    """
    results = []
    cur_topic = topic
    cur_sections = {}
    cur_label = None
    preamble = []

    def flush():
        if cur_sections:
            results.append((cur_topic, cur_sections))

    for raw in text.splitlines():
        line = raw.rstrip()
        sec = match_section(line) if line.strip() else None
        if sec:
            label, inline = sec
            heading_name = _clean_title(_split_heading(line)[0])
            chunk = (heading_name, [inline] if inline else [])
            cur_sections.setdefault(label, []).append(chunk)
            cur_label = label
            continue

        new_topic = match_topic(line) if line.strip() else None
        if new_topic and topic is None and (cur_sections or cur_topic is None):
            if cur_sections:
                flush()
                cur_sections = {}
            cur_topic = new_topic
            cur_label = None
            continue

        if cur_label:
            cur_sections[cur_label][-1][1].append(line)
        elif line.strip():
            preamble.append(line.strip())

    flush()

    # No title anywhere: fall back to the first preamble line if it is short.
    if results and results[0][0] is None and preamble and len(preamble[0]) <= 80:
        results[0] = (_clean_title(preamble[0]), results[0][1])
    return results


def _tidy(lines):
    # drop leading/trailing blanks and markdown rules, collapse blank runs
    lines = [l for l in lines if not re.match(r"^\s*([-*_]\s*){3,}$", l)]
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


# ---------------------------------------------------------------------------
# Markdown-ish -> Anki HTML
# ---------------------------------------------------------------------------
_BULLET_RE = re.compile(r"^(\s*)(?:[-*+•▪●]|\d+[.)])\s+(.*)$")


def _inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: "<b>%s</b>" % (m.group(1) or m.group(2)), s)
    s = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<i>\1</i>", s)
    s = re.sub(r"`(.+?)`", r"\1", s)
    return s


def to_html(lines):
    out = []
    stack = []  # list of (indent, tag)

    def close_to(indent):
        while stack and stack[-1][0] >= indent:
            out.append("</%s>" % stack.pop()[1])

    for line in lines:
        m = _BULLET_RE.match(line)
        if m:
            indent = len(m.group(1).expandtabs(4))
            tag = "ol" if re.match(r"\s*\d", line) else "ul"
            if stack and indent < stack[-1][0]:
                close_to(indent + 1)
            if stack and stack[-1][0] == indent and stack[-1][1] != tag:
                close_to(indent)
            if not stack or indent > stack[-1][0]:
                out.append("<%s>" % tag)
                stack.append((indent, tag))
            out.append("<li>%s</li>" % _inline(m.group(2)))
        else:
            close_to(-1)
            if not line.strip():
                out.append("<br>")
            else:
                md = re.match(r"^\s*#{1,6}\s+(.*)$", line)
                text = "<b>%s</b>" % _inline(md.group(1)) if md else _inline(line.strip())
                out.append(text + "<br>")
    close_to(-1)
    result = "".join(out)
    result = re.sub(r"(<br>)+$", "", result)
    result = re.sub(r"(<br>){3,}", "<br><br>", result)
    return result


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------
def build_cards(text, topic=None, front_template=DEFAULT_FRONT_TEMPLATE, html_out=True):
    cards = []
    order = [label for label, _ in SECTIONS]
    for t, sections in parse(text, topic):
        t = t or "Untitled"
        for label in order:
            if label not in sections:
                continue
            chunks = [(h, _tidy(lines)) for h, lines in sections[label]]
            chunks = [(h, lines) for h, lines in chunks if lines]
            if not chunks:
                continue
            if len(chunks) == 1:
                body = chunks[0][1]
            else:  # e.g. separate "Signs and Symptoms" and "Physical Exam" headings
                body = []
                for h, lines in chunks:
                    body += (["", "**%s**" % h] if body else ["**%s**" % h]) + lines
            back = to_html(body) if html_out else "\n".join(body)
            cards.append({
                "topic": t,
                "section": label,
                "front": front_template.format(topic=t, section=label),
                "back": back,
            })
    return cards


# ---------------------------------------------------------------------------
# AnkiConnect
# ---------------------------------------------------------------------------
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
            "Can't reach AnkiConnect at %s (%s).\n"
            "  -> Is Anki open? Is the AnkiConnect add-on (code 2055492159) installed?" % (url, e)
        )
    if resp.get("error"):
        raise AnkiError(resp["error"])
    return resp.get("result")


def send_to_anki(cards, deck, model, front_field, back_field, tags, allow_dupes, url):
    anki("createDeck", url=url, deck=deck)
    fields = anki("modelFieldNames", url=url, modelName=model)
    for f in (front_field, back_field):
        if f not in fields:
            raise AnkiError("Note type %r has no field %r (it has: %s). Use --model/--front-field/--back-field."
                            % (model, f, ", ".join(fields)))
    added, skipped = 0, []
    for c in cards:
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
            print("  + %s" % c["front"])
        except AnkiError as e:
            skipped.append((c["front"], str(e)))
            print("  ! %s  (%s)" % (c["front"], e))
    return added, skipped


def write_tsv(cards, path, tags, deck):
    tag_str = " ".join(tags)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("#separator:tab\n#html:true\n#deck:%s\n#tags column:3\n" % deck)
        for c in cards:
            row = [c["front"], c["back"], tag_str]
            f.write("\t".join(x.replace("\t", " ").replace("\n", "<br>") for x in row) + "\n")


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def preview(cards):
    for c in cards:
        print("=" * 70)
        print("FRONT:", c["front"])
        print("BACK: ", c["back"])
    print("=" * 70)
    print("%d card(s)" % len(cards))


def process(text, args):
    cards = build_cards(text, args.topic, args.front)
    if not cards:
        print("No sections found. Expected headings like 'Definition', 'Etiology', 'Diagnosis'...")
        return 0
    topics = sorted({c["topic"] for c in cards})
    print("Found %d card(s) for: %s" % (len(cards), ", ".join(topics)))
    if args.dry_run:
        preview(cards)
        return len(cards)
    if args.tsv:
        write_tsv(cards, args.tsv, args.tags, args.deck)
        print("Wrote %s - import it in Anki with File > Import (deck %r)." % (args.tsv, args.deck))
        return len(cards)
    added, skipped = send_to_anki(cards, args.deck, args.model, args.front_field, args.back_field,
                                  args.tags, args.allow_duplicates, args.url)
    print("Added %d card(s) to %r%s." % (added, args.deck,
                                         ", skipped %d" % len(skipped) if skipped else ""))
    return added


def watch(args):
    print("Watching the clipboard. Copy a ChatGPT answer and its cards go into %r." % args.deck)
    print("Press Ctrl-C to stop.\n")
    last = None
    try:
        last = get_clipboard()  # ignore whatever was already copied
    except RuntimeError as e:
        sys.exit(str(e))
    while True:
        try:
            time.sleep(args.interval)
            text = get_clipboard()
            if text and text != last:
                last = text
                if len(build_cards(text, args.topic, args.front)) >= args.min_sections:
                    print(time.strftime("[%H:%M:%S] ") + "New ChatGPT answer detected")
                    try:
                        process(text, args)
                    except AnkiError as e:
                        print("  Error: %s" % e)
                    print()
        except KeyboardInterrupt:
            print("\nStopped.")
            return


def main(argv=None):
    p = argparse.ArgumentParser(description="Turn ChatGPT answers into one Anki card per section.")
    p.add_argument("file", nargs="?", help="text/markdown file (default: paste into stdin)")
    p.add_argument("-d", "--deck", default=DEFAULT_DECK, help="Anki deck, e.g. 'Medicine::Cardio' (created if missing)")
    p.add_argument("-t", "--topic", help="topic name for the card fronts (default: taken from the answer's title)")
    p.add_argument("-c", "--clipboard", action="store_true", help="read the answer from the clipboard")
    p.add_argument("-w", "--watch", action="store_true", help="keep watching the clipboard and add cards automatically")
    p.add_argument("--interval", type=float, default=1.0, help="clipboard poll interval in seconds (watch mode)")
    p.add_argument("--min-sections", type=int, default=2, help="sections needed before watch mode treats a copy as an answer")
    p.add_argument("-n", "--dry-run", action="store_true", help="show the cards, send nothing")
    p.add_argument("--tsv", metavar="PATH", help="write an Anki import file instead of using AnkiConnect")
    p.add_argument("--model", default=DEFAULT_MODEL, help="note type (default: Basic)")
    p.add_argument("--front-field", default="Front")
    p.add_argument("--back-field", default="Back")
    p.add_argument("--front", default=DEFAULT_FRONT_TEMPLATE, help="card front template, uses {topic} and {section}")
    p.add_argument("--tags", default="gpt-to-anki", help="space-separated tags")
    p.add_argument("--allow-duplicates", action="store_true", help="add even if an identical front exists in the deck")
    p.add_argument("--url", default=ANKI_CONNECT_URL, help="AnkiConnect URL")
    args = p.parse_args(argv)
    args.tags = args.tags.split()

    if args.watch:
        watch(args)
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
        process(text, args)
    except AnkiError as e:
        print("Error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
