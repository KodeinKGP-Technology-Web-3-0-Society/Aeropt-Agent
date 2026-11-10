import unittest

from mock_test_agent.derived_items import accept_quoted_result, extract_final_value, from_model_reply, make_single_correct


class TestDerivedAnswers(unittest.TestCase):
    def test_two_results_are_not_used(self):
        text = r"The limits are $300 \text{ nm}$ and $525 \text{ nm}$."
        self.assertIsNone(extract_final_value(text))

    def test_last_equals_is_copied(self):
        text = r"$$\mu = 1.33 \times \frac{2.25}{2.00} = 1.50.$$"
        self.assertEqual(extract_final_value(text), "1.50")

    def test_plain_ratio(self):
        text = r"$$\frac{I_{\max}}{I_{\min}} = 34.$$"
        self.assertEqual(extract_final_value(text), "34")

    def test_options_keep_the_printed_answer_exactly_once(self):
        text = r"$$\mu = 1.33 \times \frac{2.25}{2.00} = 1.50.$$"
        made = make_single_correct(text, "ex_1")
        self.assertIsNotNone(made)
        texts = [option["text"] for option in made["options"]]
        self.assertEqual(texts.count("1.50"), 1)
        letter = made["answer"]["letters"]
        correct = next(option["text"] for option in made["options"] if option["label"] == letter)
        self.assertEqual(correct, "1.50")
        self.assertEqual(len(set(texts)), 4)

    def test_no_solution_means_no_question(self):
        self.assertIsNone(make_single_correct("", "ex_2"))
        self.assertIsNone(make_single_correct("Discuss the properties of the lens.", "ex_3"))

    def test_model_value_must_be_printed_in_the_solution(self):
        solution = r"Substituting gives $$\mu = 1.50.$$"
        self.assertIsNone(accept_quoted_result(solution, r"$$\mu = 1.50.$$", "2.50"))
        self.assertEqual(accept_quoted_result(solution, r"$$\mu = 1.50.$$", "1.50"), "1.50")
        reply = '{"quote":"$$\\\\mu = 1.50.$$","value":"1.50","distractors":["1.50","3.00","0.75","1.65"]}'
        made = from_model_reply(solution, "ex_4", reply)
        self.assertIsNotNone(made)
        texts = [option["text"] for option in made["options"]]
        self.assertEqual(texts.count("1.50"), 1)

    def test_two_results_in_the_quote_are_rejected(self):
        solution = "The limits are 300 nm and 525 nm."
        self.assertIsNone(accept_quoted_result(solution, solution, "300 nm"))


if __name__ == "__main__":
    unittest.main()
