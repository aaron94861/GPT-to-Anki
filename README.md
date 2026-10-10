# GPT-to-Anki

Click **Copy** under a ChatGPT reply, and the card appears in the right Anki subdeck. You never open Anki's Add window.

## Before and after

**Before (per section, 6 times per disease):**
ChatGPT → Project (Cardiology) → chat (Endocarditis) → copy the reply → Anki → find the
Internal Medicine::Cardiology::Endocarditis deck → Browse → Notes → Add Notes → type "Definition?" →
paste the back → Add → back to ChatGPT …

**After:**

```
ChatGPT reply "Endocarditis – Definition"   ── click Copy ──▶  Anki: Internal Medicine::Cardiology::Endocarditis
                                                                  Front: Definition?
                                                                  Back:  the reply (formatting kept)
ChatGPT reply "Endocarditis – Aetiology…"   ── click Copy ──▶     Front: Aetiology and classification?
...
```

The script reads the disease name and the section from the reply's title, finds the subdeck that already
exists in Anki (or creates one under your parent deck), and adds the card using your
**Syllabus Topic Prompt** note type.

| ChatGPT reply about… | Card front |
| --- | --- |
| Definition | `Definition?` |
| Aetiology / Classification / Causes / Risk factors | `Aetiology and classification?` |
| Pathogenesis / Pathophysiology | `Pathogenesis?` |
| Physical examination / Signs and symptoms / Clinical features | `Physical examination and signs and symptoms?` |
| Diagnosis / Investigations / Workup | `Diagnosis?` |
| Management / Treatment | `Management and treatment?` |

## One-time setup (Windows)

1. **Install Python** from https://www.python.org/downloads/ and tick **"Add Python to PATH"** during setup.
2. **Install AnkiConnect**: in Anki, go to *Tools → Add-ons → Get Add-ons…*, enter `2055492159`, then restart Anki.
3. **Download this folder** (green *Code* button → *Download ZIP*) and unzip it somewhere, e.g. your Documents.
4. Optional: paste [`CHATGPT_PROJECT_INSTRUCTIONS.md`](CHATGPT_PROJECT_INSTRUCTIONS.md) into each ChatGPT project's
   instructions. This keeps reply titles in the "Disease – Section" form the script reads.

## Daily use

1. Open Anki.
2. Double-click **`Watch Cardiology.bat`**. A black window opens; leave it open.
3. In ChatGPT, open the disease chat and click **Copy** under each reply (Definition, Aetiology, …).
   For each copy, the window shows something like:
   ```
   [19:42:10] Copied: Endocarditis - Definition
     + Definition?                    -> Internal Medicine::Cardiology::Endocarditis
   ```
4. Close the window when you're done.

**Other projects:** copy `Watch Cardiology.bat`, rename it (e.g. `Watch Endocrinology.bat`), and change the
`PARENT=` line inside. The parent is only used to create subdecks for **new** diseases; diseases that already
have a subdeck are found wherever it is.

**Notes:**
- If a reply has no disease name in its title (just "Diagnosis"), the card uses the disease from your previous copy.
- If the subdeck already has that front (e.g. "Diagnosis?"), the card is **skipped**, so copying twice is harmless.
  To replace the old card's back instead, run with `--update`.
- Ordinary copies (a sentence, a word) are ignored.

## Other ways to run it

```bat
py gpt_to_anki.py --clipboard                       :: add the reply that's on the clipboard, once
py gpt_to_anki.py answer.md --dry-run               :: preview cards, add nothing
py gpt_to_anki.py --watch --deck "Some::Deck"       :: put every card in one exact deck
py gpt_to_anki.py answer.md --tsv cards.tsv         :: make a file for Anki's File > Import
```

(On Mac/Linux use `python3` instead of `py`.)

| Option | What it does |
| --- | --- |
| `-w, --watch` | Keep watching the clipboard |
| `-p, --parent` | Where new disease subdecks are created, e.g. `"Internal Medicine::Cardiology"` |
| `-d, --deck` | Put every card in this exact deck instead |
| `-t, --topic` | Disease name, if the replies don't contain it |
| `--update` | Replace the back of an existing card with the same front |
| `--no-title` | Leave the "Endocarditis – Definition" heading off the back |
| `--front` | Front template, default `{section}?`; e.g. `"{topic} – {section}?"` |
| `--model` | Note type, default `Syllabus Topic Prompt` (falls back to `Basic` if missing) |
| `-n, --dry-run` | Show the cards without adding them |

To set defaults permanently (parent deck, note type, tags), or to rename or add card types, edit the
**YOUR SETTINGS** block at the top of `gpt_to_anki.py`.

## Also works with one big reply

If you ask ChatGPT for every section in one reply (one `# Disease` title with a `## Section` heading per
section), a single copy makes all six cards. See `examples/all_sections_in_one_reply.md`.

## Tests

```bash
python -m unittest discover -s tests
```
