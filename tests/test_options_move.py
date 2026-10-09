from agents.learning_agent import _decide_move_and_reveal

LESSON = {"interaction_count": 1, "classroom_surface": "lesson"}


def test_options_request_gets_choices_not_a_probe():
    move, reveal = _decide_move_and_reveal("give me options", LESSON)
    assert reveal == 2 and "A, B, C, D" in move


def test_explicit_answer_request_still_escalates():
    assert _decide_move_and_reveal("tell me the answer", LESSON)[1] == 3


def test_plain_question_in_a_lesson_still_withholds():
    assert _decide_move_and_reveal("what is torque", LESSON)[1] == 0


def test_general_chat_lets_the_tutor_choose_the_mode():
    # No room surface: the tutor reads the intent itself, for every kind of message.
    for query in ("why does uniform circular motion accelerate?", "give me a jee question", "just give me a hint"):
        move, reveal = _decide_move_and_reveal(query, {"interaction_count": 1})
        assert "choose the mode yourself" in move and isinstance(reveal, str)


def test_general_chat_question_request_withholds_the_solution():
    move, _ = _decide_move_and_reveal("give me a question", {"interaction_count": 1})
    assert "ONLY the question" in move and "No solution" in move
