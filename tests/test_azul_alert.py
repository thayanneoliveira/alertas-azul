from src.azul_alert import Config, FlightOffer, filter_offers, normalize_points


def config() -> Config:
    return Config(
        origin="CNF",
        destination="SLZ",
        year=2026,
        month=9,
        max_points=6000,
        email_to="teste@example.com",
        resend_api_key="re_test",
        email_from="Alertas Azul <onboarding@resend.dev>",
    )


def test_normalize_points() -> None:
    assert normalize_points("6.000 pontos") == 6000


def test_filter_offers_matches_rule() -> None:
    offers = [
        FlightOffer("14/09/2026", "CNF", "SLZ", 5800, "Econômica", "https://example.com"),
        FlightOffer("15/09/2026", "CNF", "SLZ", 9000, "Econômica", "https://example.com"),
        FlightOffer("14/10/2026", "CNF", "SLZ", 5000, "Econômica", "https://example.com"),
    ]

    matches = filter_offers(offers, config())

    assert len(matches) == 1
    assert matches[0].points == 5800
