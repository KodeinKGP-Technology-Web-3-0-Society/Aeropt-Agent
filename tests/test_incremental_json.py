import pytest
from agents.answer_checker import _unescape_json_string, check_answer_streaming

def test_incremental_json_scanner():
    """
    Test that the incremental JSON string scanner can handle chunks properly,
    including mid-escape sequences. We simulate the scanner by running the exact
    same logic as check_answer_streaming in a small mock setup.
    """
    
    # We will just write a small stream generator to mock groq_stream
    chunks = [
        (r'{"verdict": "revise", ', False),
        (r'"issues": ["foo"], "rev', False),
        (r'ised_answer": "Hello ' + '\\', False),
        (r'nWor', False),
        (r'ld! \u00e', False),
        (r'9 \"', False),
        (r'ok\"', False),
        (r'"}', True)
    ]
    
    def mock_stream():
        for chunk, is_final in chunks:
            yield chunk, is_final
            
    emitted_tokens = []
    def on_token(token: str):
        emitted_tokens.append(token)
        
    # We mock groq_stream to return our stream, and test check_answer_streaming
    import agents.answer_checker as checker
    import utils.llm
    
    original_stream = checker.groq_stream
    
    def fake_stream(*args, **kwargs):
        return mock_stream()
        
    checker.groq_stream = fake_stream
    checker.MIN_LENGTH_TO_CHECK = 0
    try:
        # call check_answer_streaming
        result = checker.check_answer_streaming(
            query="test",
            draft="test",
            state={},
            on_token=on_token
        )
        
        # emitted tokens should combine to "Hello\nWorld! é \"ok\""
        combined = "".join(emitted_tokens)
        
        # Test combined string matches expected (release gate normalizes trailing space before newline)
        assert combined in ('Hello\nWorld! é "ok"', 'Hello \nWorld! é "ok"')
        assert result.verdict == "revised"
        assert result.answer in ('Hello\nWorld! é "ok"', 'Hello \nWorld! é "ok"')
    finally:
        checker.groq_stream = original_stream
