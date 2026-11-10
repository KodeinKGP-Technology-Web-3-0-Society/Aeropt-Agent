"""Mock-test settings.

These live here so the shared ``config.py`` does not change. Debrief model
names are read from the app config only when a debrief is actually generated.
"""

MOCK_TEST_MAX_QUESTIONS_PER_SUBJECT = 60
# How many worked solutions a single mock build may ask a model to read.
# Results are cached, so later builds reuse them instead of calling again.
MOCK_SOLUTION_READS = 12
MOCK_TEST_DEFAULT_TIME_MINUTES = 180
MOCK_TEST_DIFFICULTY_DEFAULTS = {"JEE_ADVANCED": 4, "JEE_MAIN": 3, "custom": 3}
# What the student picks (single / multiple / numerical) -> the stored question_type values.
QUESTION_TYPE_GROUPS = {
    "single": ("single_correct", "comprehension"),
    "multiple": ("multiple_correct", "multi_correct"),
    "numerical": ("numeric", "numerical", "integer"),
}
EXAM_ALLOWED_DB = {
    "JEE_ADVANCED": ("Jee Advanced", "Jee main and Advanced"),
    "JEE_MAIN": ("Jee main", "Jee main and Advanced"),
    "custom": (),
}


def __getattr__(name: str):
    if name == "MOCK_DEBRIEF_MODEL":
        import config
        return config.GEMINI_MODEL
    if name == "MOCK_DEBRIEF_FALLBACK_MODELS":
        import config
        return config.FALLBACK_MODELS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
