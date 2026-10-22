from collections.abc import Iterable

ROLE_POINTS = {
    "professional": 30,
    "investor": 30,
    "researcher": 10,
    "journalist": 10,
    "public_sector": 10,
    "student": 3,
    "other": 5,
}
CORPORATE_ROLES = frozenset({"professional", "investor"})
SEGMENT_POINTS = {
    "generator": 20,
    "storage_developer": 20,
    "financial": 15,
    "trader": 15,
    "consultancy": 15,
    "utility": 10,
    "equipment": 10,
    "consumer": 10,
    "law_regulatory": 5,
    "other": 0,
}
COMMERCIAL_INTERESTS = frozenset(
    {"monitor_losses", "storage", "free_market", "data_api", "compensation", "investment"}
)
FREE_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "yahoo.com",
        "yahoo.com.br",
        "icloud.com",
        "bol.com.br",
        "uol.com.br",
        "terra.com.br",
        "proton.me",
        "protonmail.com",
    }
)
SIZE_POINTS = {"201-1000": 5, "1000+": 5}
HOT_THRESHOLD = 60
WARM_THRESHOLD = 35
INTEREST_POINTS = 5
INTEREST_CAP = 15


def lead_tier(score: int) -> str:
    if score >= HOT_THRESHOLD:
        return "hot"
    if score >= WARM_THRESHOLD:
        return "warm"
    return "cold"


def score_lead(
    *,
    role: str,
    email: str,
    organization: str | None,
    segment: str | None,
    company_size: str | None,
    plants: Iterable[str],
    interests: Iterable[str],
    phone: str | None,
    marketing_opt_in: bool,
) -> tuple[int, str]:
    corporate = role in CORPORATE_ROLES
    score = ROLE_POINTS.get(role, 0)
    if corporate and organization:
        score += 15
        domain = email.rsplit("@", 1)[-1].lower()
        if domain not in FREE_EMAIL_DOMAINS:
            score += 10
    if corporate and segment:
        score += SEGMENT_POINTS.get(segment, 0)
    if corporate and company_size:
        score += SIZE_POINTS.get(company_size, 0)
    plant_count = len(list(plants))
    if plant_count:
        score += 10
    if plant_count >= 3:
        score += 5
    commercial = sum(1 for interest in set(interests) if interest in COMMERCIAL_INTERESTS)
    score += min(commercial * INTEREST_POINTS, INTEREST_CAP)
    if phone:
        score += 5
    if marketing_opt_in:
        score += 5
    return score, lead_tier(score)
