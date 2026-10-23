"""Term similarity: the built-in string similarity."""

from accord_mce_metrics import terms as T


def test_string_similarity_normalises_case_and_punctuation():
    assert T.string_similarity("Error-Rate!", "error rate") == 1.0
    assert T.string_similarity("", "") == 1.0
    assert T.string_similarity("x", "") == 0.0
    assert 0.0 < T.string_similarity("error rate", "failure rate") < 1.0


def test_the_scorer_is_the_built_in_one():
    scorer, threshold, backend = T.load_scorer(None)
    assert scorer is T.string_similarity and threshold == T.SIM_MERGE == 0.72 and backend == T.BUILTIN_BACKEND
    assert T.load_scorer(0.5)[1] == 0.5  # an explicit threshold wins
