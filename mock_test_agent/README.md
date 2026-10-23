# Mock Test Agent

This folder is the whole mock-test feature. It is not connected to the running tutor.

- `api_server.py`, `config.py`, and the shared test setup are unchanged.
- Routes exist in `router.py` but are not mounted, so `/api/v1/mock/*` is not live.
- `migrations/028_mock_tests.sql` creates the three mock-test tables. It has not been applied.

To turn the API on later, add these two lines to `api_server.py`:

```python
from mock_test_agent.router import router as mock_test_router
app.include_router(mock_test_router)
```

Run the tests with:

```bash
python -m pytest mock_test_agent/tests -q
```

The design write-up is `MOCK_TEST_AGENT.md`.
