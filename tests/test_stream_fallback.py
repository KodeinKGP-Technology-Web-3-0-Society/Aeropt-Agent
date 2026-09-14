import pytest
from groq import InternalServerError
from utils.llm import groq_stream

class MockDelta:
    def __init__(self, content):
        self.content = content

class MockChoice:
    def __init__(self, content):
        self.delta = MockDelta(content)

class MockChunk:
    def __init__(self, content):
        self.choices = [MockChoice(content)]

class MockFailingStream:
    def __init__(self, chunks_before_failure, exc):
        self.chunks_before_failure = chunks_before_failure
        self.exc = exc
        self.idx = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.idx < len(self.chunks_before_failure):
            chunk = self.chunks_before_failure[self.idx]
            self.idx += 1
            return MockChunk(chunk)
        raise self.exc

class MockSuccessStream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.idx = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.idx < len(self.chunks):
            chunk = self.chunks[self.idx]
            self.idx += 1
            return MockChunk(chunk)
        raise StopIteration

class MockCompletions:
    def __init__(self, streams):
        self.streams = streams
        self.call_count = 0

    def create(self, **kwargs):
        stream = self.streams[self.call_count]
        self.call_count += 1
        return stream

class MockChat:
    def __init__(self, streams):
        self.completions = MockCompletions(streams)

class MockClient:
    def __init__(self, streams):
        self.chat = MockChat(streams)

def test_stream_fallback_mid_stream(monkeypatch):
    import utils.llm
    # Prevent backoff sleeping during test
    monkeypatch.setattr(utils.llm, "BACKOFF_BASE", 0.001)
    monkeypatch.setattr("utils.groq_pool.get_pool", lambda: None)

    from groq import APIConnectionError
    import httpx
    exc = APIConnectionError(request=httpx.Request("GET", "http://test"))

    streams = [
        # First attempt fails mid stream
        MockFailingStream(["hello", " there"], exc),
        # Second attempt (retry 1) fails immediately
        MockFailingStream([], exc),
        # Third attempt (retry 2) fails mid stream
        MockFailingStream(["almost"], exc),
        # Fourth attempt (fallback model) succeeds
        MockSuccessStream(["fallback", " wins"])
    ]

    client = MockClient(streams)
    
    gen = groq_stream(
        client=client,
        model="primary-model",
        messages=[],
        fallback_models=["fallback-model"]
    )
    
    emitted = []
    model_used = None
    while True:
        try:
            chunk, is_final = next(gen)
            emitted.append((chunk, is_final))
        except StopIteration as e:
            model_used = e.value
            break
            
    assert emitted == [
        ("hello", False),
        (" there", False),
        ("almost", False),
        ("fallback", False),
        (" wins", False),
        ("", True)
    ]
    assert model_used == "fallback-model"
