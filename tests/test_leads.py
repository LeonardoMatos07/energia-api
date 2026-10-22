from app.leads import lead_tier, score_lead


def score(**overrides: object) -> tuple[int, str]:
    values: dict[str, object] = {
        "role": "professional",
        "email": "ana@parque.com.br",
        "organization": "Parque SA",
        "segment": "generator",
        "company_size": "201-1000",
        "plants": ["a"],
        "interests": ["monitor_losses"],
        "phone": None,
        "marketing_opt_in": False,
    }
    values.update(overrides)
    return score_lead(**values)  # type: ignore[arg-type]


def test_a_corporate_generator_with_plants_is_a_hot_lead() -> None:
    value, tier = score(plants=["a", "b", "c"], interests=["monitor_losses", "storage"])
    assert tier == "hot"
    assert value >= 60


def test_a_student_is_cold_even_with_interests() -> None:
    value, tier = score(
        role="student",
        organization=None,
        segment=None,
        company_size=None,
        plants=[],
        interests=["research"],
    )
    assert tier == "cold"
    assert value < 35


def test_a_free_email_domain_scores_below_a_corporate_domain() -> None:
    corporate, _ = score(email="ana@parque.com.br")
    personal, _ = score(email="ana@gmail.com")
    assert corporate - personal == 10


def test_commercial_interests_are_capped() -> None:
    many, _ = score(
        interests=["monitor_losses", "storage", "free_market", "data_api", "investment"]
    )
    three, _ = score(interests=["monitor_losses", "storage", "free_market"])
    assert many == three


def test_organization_details_only_count_for_corporate_roles() -> None:
    researcher, _ = score(role="researcher", plants=[], interests=[])
    assert researcher == 10


def test_tier_thresholds() -> None:
    assert [lead_tier(value) for value in (0, 34, 35, 59, 60, 120)] == [
        "cold",
        "cold",
        "warm",
        "warm",
        "hot",
        "hot",
    ]
