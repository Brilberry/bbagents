from examples.heterogeneous_model_organization import run_example


def test_heterogeneous_model_organization_uses_independent_models() -> None:
    draft, review, selections = run_example()

    assert draft.startswith("Draft:")
    assert review.startswith("Review:")
    assert [selection["model_id"] for selection in selections] == [
        "fast-local",
        "review-local",
    ]
