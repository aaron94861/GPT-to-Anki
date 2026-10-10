# GPT-to-Anki

Copy a ChatGPT answer, get one Anki card per section, filed in the deck you choose.

```
ChatGPT answer                         Anki deck "Medicine::GI"
# Acute Pancreatitis                   ┌──────────────────────────────────────────────┐
## Definition         ───────────────▶ │ Front: Acute Pancreatitis - Definition       │
## Etiology           ───────────────▶ │ Front: Acute Pancreatitis - Etiology         │
## Pathogenesis       ───────────────▶ │ Front: Acute Pancreatitis - Pathogenesis     │
## Physical Exam & S/S ──────────────▶ │ Front: ... - Physical Examination & Signs... │
## Diagnosis          ───────────────▶ │ Front: Acute Pancreatitis - Diagnosis        │
## Management & Tx    ───────────────▶ │ Front: ... - Management & Treatment          │
                                       └──────────────────────────────────────────────┘
```

**Before:** copy one section → open Anki → Add → choose deck → type front → paste back → repeat 6× per disease.
**After:** copy the whole ChatGPT answer → 6 cards are in Anki.

## One-time setup

1. **Python 3.8+**: https://www.python.org/downloads/ (on Windows, tick "Add Python to PATH").
2. **AnkiConnect add-on**: in Anki, go to *Tools → Add-ons → Get Add-ons…*, enter code `2055492159`, then restart Anki.
3. **ChatGPT project instructions**: paste the block from
   [`CHATGPT_PROJECT_INSTRUCTIONS.md`](CHATGPT_PROJECT_INSTRUCTIONS.md) into your ChatGPT project's
   instructions so every answer uses the same six headings.
4. Download `gpt_to_anki.py` (no extra packages needed).

Keep Anki open while you use the script.

## Using it

### Fastest: watch mode (copy and you're done)

```bash
python gpt_to_anki.py --watch --deck "Medicine::GI"
```

Leave it running. In ChatGPT, click the **Copy** button under an answer, and the cards are added within a
second. Copy the next answer, and those cards are added too. Ordinary copies, like a single word, are
ignored: a copy has to contain at least 2 recognised section headings.

### One answer at a time

```bash
python gpt_to_anki.py --clipboard --deck "Medicine::GI"     # use what's on the clipboard
python gpt_to_anki.py --deck "Medicine::GI"                 # paste into the terminal, then Ctrl-D (Mac/Linux) / Ctrl-Z Enter (Windows)
python gpt_to_anki.py answer.md --deck "Medicine::GI"       # from a saved file
```

### Check before adding

```bash
python gpt_to_anki.py examples/sample_chatgpt_answer.md --dry-run
```

### No AnkiConnect? Make an import file

```bash
python gpt_to_anki.py answer.md --deck "Medicine::GI" --tsv cards.tsv
```

Then in Anki use *File → Import → cards.tsv*.

## Options

| Option | What it does | Default |
| --- | --- | --- |
| `-d, --deck` | Deck to add to (created if missing). Use `::` for subdecks. | `Default` |
| `-t, --topic` | Override the disease name on the card fronts | taken from the `# Title` |
| `--front` | Front text template, using `{topic}` and `{section}` | `{topic} - {section}` |
| `--tags` | Space-separated tags | `gpt-to-anki` |
| `--model` / `--front-field` / `--back-field` | Use another note type | `Basic` / `Front` / `Back` |
| `--allow-duplicates` | Add the card even if the same front already exists in the deck | off (skips duplicates) |
| `-n, --dry-run` | Show the cards without adding them | |
| `--tsv PATH` | Write an import file instead of using AnkiConnect | |

Example: question-style fronts:
`--front "What is the {section} of {topic}?"`

## What the parser understands

- Headings in any of these forms: `## Definition`, `**Definition**`, `**Definition:** text…`, `1. Definition`, `Definition: text…`
- Common synonyms: *Pathophysiology* → Pathogenesis, *Causes / Risk factors* → Etiology,
  *Clinical presentation / Signs and Symptoms / Physical Exam* → Physical Examination & Signs and Symptoms,
  *Workup / Investigations* → Diagnosis, *Treatment / Management* → Management & Treatment.
  When an answer has separate *Signs and Symptoms* and *Physical Exam* headings, both go on one card under bold sub-headings.
- Several diseases in one paste (each starting with its own `# Title`)
- Bullets, numbered lists, **bold**, and *italics* are kept as formatting on the card.

To add, rename, or split cards (for example, a *Complications* card), edit the `SECTIONS` list at the top of
`gpt_to_anki.py`.

## Tests

```bash
python -m unittest discover -s tests
```
