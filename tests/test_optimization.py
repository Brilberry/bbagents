from bbagents.optimization import ModelProfile, route_model


def test_router_selects_cheapest_eligible_model() -> None:
    cheap = ModelProfile("cheap", 1, 2, 0.5, 0.7)
    capable = ModelProfile("capable", 5, 10, 1.0, 0.95)

    assert route_model([cheap, capable], minimum_quality=0.9) == capable
    assert capable.estimate_cost(1_000_000, 500_000) == 10

