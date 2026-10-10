import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import gpt_to_anki as g

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")

FRONTS = [
    "Definition?",
    "Aetiology and classification?",
    "Pathogenesis?",
    "Physical examination and signs and symptoms?",
    "Diagnosis?",
    "Management and treatment?",
]


def example(name):
    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as f:
        return f.read()


class SingleReplyTests(unittest.TestCase):
    """One copied ChatGPT reply = one card (the workflow in the PDF)."""

    def test_definition_reply(self):
        cards = g.build_cards(example("endocarditis_definition.md"))
        self.assertEqual(len(cards), 1)
        c = cards[0]
        self.assertEqual((c["topic"], c["front"]), ("Endocarditis", "Definition?"))
        self.assertTrue(c["back"].startswith("<b>Endocarditis – Definition</b>"))
        self.assertIn("<li><b>Valvular destruction and regurgitation</b>", c["back"])
        self.assertIn("<b>Key distinction:</b>", c["back"])

    def test_subheadings_stay_on_one_card(self):
        cards = g.build_cards(example("endocarditis_aetiology.md"))
        self.assertEqual([c["front"] for c in cards], ["Aetiology and classification?"])
        back = cards[0]["back"]
        for part in ("<b>Classification</b>", "<b>Aetiology</b>", "<b>Risk factors</b>", "<table", "HACEK"):
            self.assertIn(part, back)

    def test_title_variants(self):
        for title in ("# Endocarditis – Pathogenesis", "**Endocarditis - Pathophysiology**",
                      "## Endocarditis: Pathogenesis", "**Pathogenesis of Endocarditis**",
                      "### Pathogenesis — Endocarditis"):
            cards = g.build_cards(title + "\n\nVegetations form on damaged endothelium.\n")
            self.assertEqual([(c["topic"], c["front"]) for c in cards], [("Endocarditis", "Pathogenesis?")], title)

    def test_section_names_map_to_six_cards(self):
        for heading, front in [("Physical Examination", FRONTS[3]), ("Signs and Symptoms", FRONTS[3]),
                               ("Clinical Features", FRONTS[3]), ("Investigations", FRONTS[4]),
                               ("Treatment", FRONTS[5]), ("Management & Treatment", FRONTS[5]),
                               ("Etiology", FRONTS[1]), ("Classification", FRONTS[1]),
                               ("Aetiology and Classification", FRONTS[1])]:
            cards = g.build_cards("**Endocarditis – %s**\n\ntext\n" % heading)
            self.assertEqual([c["front"] for c in cards], [front], heading)

    def test_inner_bold_dash_line_is_content(self):
        text = ("**Endocarditis – Diagnosis**\n\n**Duke criteria – Major**\n- Positive blood cultures\n"
                "**Duke criteria – Minor**\n- Fever\n")
        cards = g.build_cards(text)
        self.assertEqual([c["front"] for c in cards], ["Diagnosis?"])
        self.assertIn("Duke criteria – Minor", cards[0]["back"])

    def test_reply_without_disease_uses_default_topic(self):
        cards = g.build_cards("## Diagnosis\n- Modified Duke criteria\n", default_topic="Endocarditis")
        self.assertEqual([(c["topic"], c["front"]) for c in cards], [("Endocarditis", "Diagnosis?")])

    def test_bullet_with_section_word_stays_content(self):
        cards = g.build_cards("## Endocarditis – Diagnosis\n- Treatment: response to antibiotics\n")
        self.assertEqual(len(cards), 1)
        self.assertIn("Treatment: response", cards[0]["back"])

    def test_no_title_option(self):
        cards = g.build_cards(example("endocarditis_definition.md"), title_on_back=False)
        self.assertTrue(cards[0]["back"].startswith("<b>Endocarditis</b> is"))

    def test_html_escaping(self):
        cards = g.build_cards("## Hyponatraemia – Definition\nNa < 135 & K > 5\n")
        self.assertIn("Na &lt; 135 &amp; K &gt; 5", cards[0]["back"])


class MultiSectionTests(unittest.TestCase):
    def test_all_sections_in_one_reply(self):
        cards = g.build_cards(example("all_sections_in_one_reply.md"))
        self.assertEqual([c["front"] for c in cards], FRONTS)
        self.assertEqual({c["topic"] for c in cards}, {"Aortic Stenosis"})
        self.assertIn("<ol>", cards[4]["back"])

    def test_separate_signs_and_exam_headings_merge(self):
        text = "# Pneumonia\n## Signs and Symptoms\n- Fever\n## Physical Examination\n- Crackles\n"
        cards = g.build_cards(text)
        self.assertEqual(len(cards), 1)
        self.assertIn("<b>Signs and Symptoms</b>", cards[0]["back"])
        self.assertIn("<b>Physical Examination</b>", cards[0]["back"])

    def test_multiple_diseases(self):
        text = "# A\n## Definition\na\n# B\n## Definition\nb\n## Treatment\nt\n"
        cards = g.build_cards(text)
        self.assertEqual([(c["topic"], c["front"]) for c in cards],
                         [("A", "Definition?"), ("B", "Definition?"), ("B", "Management and treatment?")])


class WatchFilterTests(unittest.TestCase):
    def check(self, text, expected, default_topic=None):
        cards = g.build_cards(text, default_topic=default_topic)
        self.assertEqual(g.looks_like_answer(text, cards, 2), expected, text)

    def test_filter(self):
        self.check(example("endocarditis_definition.md"), True)
        self.check("## Diagnosis\n- Duke criteria\n", True, "Endocarditis")
        self.check("Some sentence I copied about diagnosis.", False)
        self.check("Endocarditis", False)


class DeckTests(unittest.TestCase):
    DECKS = ["Default", "Internal Medicine", "Internal Medicine::Cardiology",
             "Internal Medicine::Cardiology::Endocarditis", "Internal Medicine::Cardiology::Aortic stenosis",
             "Anaesthesiology Lectures::Lecture 11 Syncope"]

    def test_finds_existing_subdeck(self):
        self.assertEqual(g.find_decks("Endocarditis", self.DECKS),
                         ["Internal Medicine::Cardiology::Endocarditis"])
        self.assertEqual(g.find_decks("Aortic Stenosis", self.DECKS),
                         ["Internal Medicine::Cardiology::Aortic stenosis"])
        self.assertEqual(g.find_decks("Infective endocarditis", self.DECKS),
                         ["Internal Medicine::Cardiology::Endocarditis"])
        self.assertEqual(g.find_decks("Myocarditis", self.DECKS), [])


class FakeAnki(BaseHTTPRequestHandler):
    calls = []
    models = ["Basic", "Syllabus Topic Prompt"]
    decks = ["Default", "Internal Medicine::Cardiology::Endocarditis"]
    existing = {("Internal Medicine::Cardiology::Endocarditis", "Diagnosis?")}

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAnki.calls.append(req)
        a, prm = req["action"], req["params"]
        result, error = None, None
        if a == "modelNames":
            result = FakeAnki.models
        elif a == "modelFieldNames":
            result = ["Front", "Back"]
        elif a == "deckNames":
            result = FakeAnki.decks
        elif a == "addNote":
            n = prm["note"]
            if (n["deckName"], n["fields"]["Front"]) in FakeAnki.existing:
                error = "cannot create note because it is a duplicate"
            else:
                result = 1
        else:
            result = 1
        data = json.dumps({"result": result, "error": error}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class AnkiConnectTests(unittest.TestCase):
    def setUp(self):
        FakeAnki.calls = []
        FakeAnki.models = ["Basic", "Syllabus Topic Prompt"]
        self.srv = HTTPServer(("127.0.0.1", 0), FakeAnki)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.srv.server_port

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def send(self, text, parent=""):
        cards = g.build_cards(text)
        resolver = g.DeckResolver(None, parent, self.url)
        return g.send_to_anki(cards, resolver, g.NOTE_TYPE, "Front", "Back", [], url=self.url)

    def notes(self):
        return [c["params"]["note"] for c in FakeAnki.calls if c["action"] == "addNote"]

    def test_card_goes_into_existing_disease_deck(self):
        added, updated, skipped = self.send(example("endocarditis_definition.md"))
        self.assertEqual((added, skipped), (1, []))
        n = self.notes()[0]
        self.assertEqual(n["deckName"], "Internal Medicine::Cardiology::Endocarditis")
        self.assertEqual(n["modelName"], "Syllabus Topic Prompt")
        self.assertEqual(n["fields"]["Front"], "Definition?")

    def test_new_disease_goes_under_parent(self):
        self.send("**Myocarditis – Definition**\n\nInflammation of the myocardium.\n",
                  parent="Internal Medicine::Cardiology")
        self.assertEqual(self.notes()[0]["deckName"], "Internal Medicine::Cardiology::Myocarditis")

    def test_duplicate_is_skipped(self):
        added, updated, skipped = self.send("**Endocarditis – Diagnosis**\n\nModified Duke criteria\n")
        self.assertEqual((added, len(skipped)), (0, 1))

    def test_falls_back_to_basic(self):
        FakeAnki.models = ["Basic", "Cloze"]
        self.send(example("endocarditis_definition.md"))
        self.assertEqual(self.notes()[0]["modelName"], "Basic")


if __name__ == "__main__":
    unittest.main()
