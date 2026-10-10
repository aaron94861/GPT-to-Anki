import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import gpt_to_anki as g

HERE = os.path.dirname(__file__)


class ParseTests(unittest.TestCase):
    def test_sample_file_gives_six_cards(self):
        with open(os.path.join(HERE, "..", "examples", "sample_chatgpt_answer.md")) as f:
            cards = g.build_cards(f.read())
        self.assertEqual([c["section"] for c in cards], [s for s, _ in g.SECTIONS])
        self.assertEqual(cards[0]["front"], "Acute Pancreatitis - Definition")
        self.assertIn("<li>Gallstones (most common)</li>", cards[1]["back"])
        self.assertIn("<ol>", cards[4]["back"])
        self.assertIn("<b>2 of 3</b>", cards[0]["back"])

    def test_bold_and_colon_headings_with_inline_text(self):
        text = (
            "**Asthma**\n\n"
            "**Definition:** Chronic reversible airway obstruction.\n"
            "**Etiology:**\n- Atopy\n- Allergens\n"
            "Treatment: SABA, ICS\n"
        )
        cards = g.build_cards(text)
        self.assertEqual([c["section"] for c in cards],
                         ["Definition", "Etiology", "Management & Treatment"])
        self.assertEqual(cards[0]["topic"], "Asthma")
        self.assertEqual(cards[0]["back"], "Chronic reversible airway obstruction.")
        self.assertEqual(cards[2]["back"], "SABA, ICS")

    def test_numbered_headings(self):
        text = "Topic: Gout\n1. Definition\nUrate crystal arthritis\n2. Pathophysiology\nMSU deposition\n"
        cards = g.build_cards(text)
        self.assertEqual(cards[0]["topic"], "Gout")
        self.assertEqual([c["section"] for c in cards], ["Definition", "Pathogenesis"])

    def test_prose_is_not_mistaken_for_heading(self):
        text = "## Definition\nX\n## Diagnosis\nDiagnosis is made clinically.\nSymptoms vary.\n"
        cards = g.build_cards(text, topic="T")
        self.assertEqual(len(cards), 2)
        self.assertIn("Diagnosis is made clinically.", cards[1]["back"])

    def test_bullet_with_section_word_stays_content(self):
        text = "## Diagnosis\n- Treatment: response to steroids supports it\n"
        cards = g.build_cards(text, topic="T")
        self.assertEqual(len(cards), 1)
        self.assertIn("Treatment: response", cards[0]["back"])

    def test_separate_signs_and_exam_headings_merge(self):
        text = "## Signs and Symptoms\n- Fever\n## Physical Examination\n- Rales\n"
        cards = g.build_cards(text, topic="Pneumonia")
        self.assertEqual(len(cards), 1)
        self.assertIn("<b>Signs and Symptoms</b>", cards[0]["back"])
        self.assertIn("<b>Physical Examination</b>", cards[0]["back"])

    def test_multiple_topics_in_one_paste(self):
        text = "# A\n## Definition\na\n# B\n## Definition\nb\n## Treatment\nt\n"
        cards = g.build_cards(text)
        self.assertEqual([c["front"] for c in cards],
                         ["A - Definition", "B - Definition", "B - Management & Treatment"])

    def test_topic_override_and_template(self):
        cards = g.build_cards("# Ignored\n## Definition\nx\n", topic="Real", front_template="{section} of {topic}?")
        self.assertEqual(cards[0]["front"], "Definition of Real?")

    def test_html_escaping(self):
        cards = g.build_cards("## Definition\nNa < 135 & K > 5\n", topic="T")
        self.assertEqual(cards[0]["back"], "Na &lt; 135 &amp; K &gt; 5")


class FakeAnkiConnect(BaseHTTPRequestHandler):
    calls = []

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAnkiConnect.calls.append(req)
        a = req["action"]
        if a == "modelFieldNames":
            body = {"result": ["Front", "Back"], "error": None}
        elif a == "addNote" and req["params"]["note"]["fields"]["Front"].endswith("Etiology"):
            body = {"result": None, "error": "cannot create note because it is a duplicate"}
        else:
            body = {"result": 1, "error": None}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class AnkiConnectTests(unittest.TestCase):
    def test_send_creates_deck_and_adds_notes(self):
        srv = HTTPServer(("127.0.0.1", 0), FakeAnkiConnect)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = "http://127.0.0.1:%d" % srv.server_port
            cards = g.build_cards("## Definition\nd\n## Etiology\ne\n## Diagnosis\nx\n", topic="T")
            added, skipped = g.send_to_anki(cards, "Med::IM", "Basic", "Front", "Back", ["t"], False, url)
        finally:
            srv.shutdown()
        self.assertEqual(added, 2)
        self.assertEqual(len(skipped), 1)
        actions = [c["action"] for c in FakeAnkiConnect.calls]
        self.assertEqual(actions[:2], ["createDeck", "modelFieldNames"])
        note = FakeAnkiConnect.calls[2]["params"]["note"]
        self.assertEqual(note["deckName"], "Med::IM")
        self.assertEqual(note["fields"], {"Front": "T - Definition", "Back": "d"})


if __name__ == "__main__":
    unittest.main()
