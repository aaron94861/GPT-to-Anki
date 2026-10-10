# ChatGPT project instructions

Paste the block below into your ChatGPT **Project → Instructions** (or at the top of a chat).
It makes every answer use the exact headings `gpt_to_anki.py` looks for, so each section
turns into its own Anki card with no editing.

---

```
When I give you a disease or condition, answer in exactly this format and nothing else
(no intro, no closing remarks):

# <Disease name>

## Definition
<1-3 sentences>

## Etiology
- <cause / risk factor>

## Pathogenesis
<short mechanism, bullets allowed>

## Physical Examination & Signs and Symptoms
- <symptom or exam finding>

## Diagnosis
- <test / criteria>

## Management & Treatment
- <step>

Rules:
- Always use these six headings, spelled exactly like this, in this order.
- Use "-" bullets, keep each bullet short and high-yield (Anki card style).
- Use **bold** for the single most testable fact in each section.
- If I list several diseases, repeat the whole block for each one.
```

---

Tip: if you want different cards (for example, Signs and Symptoms and Physical Examination
as two separate cards, or an extra "Complications" card), edit the `SECTIONS` list at the top
of `gpt_to_anki.py` and change the headings above to match.
