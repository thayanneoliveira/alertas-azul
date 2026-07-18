from src.azul_alert import Config, normalize_points, parse_rendered_offers


def config() -> Config:
    return Config(
        origin="CNF",
        destination="SLZ",
        max_points=6000,
        email_to="teste@example.com",
        resend_api_key="re_test",
        email_from="Alertas Azul <onboarding@resend.dev>",
    )


def test_normalize_points() -> None:
    assert normalize_points("5.800 pontos") == 5800


def test_parse_rendered_offers_filters_route_and_limit() -> None:
    rendered = """
    Belo Horizonte (CNF) Para São Luís (SLZ)
    Ida: 14/09/2026
    A partir de 5.800 pontos
    Só ida

    Belo Horizonte (CNF) Para São Luís (SLZ)
    Ida: 20/10/2026
    A partir de 9.000 pontos
    Só ida

    Belo Horizonte (CNF) Para Recife (REC)
    Ida: 15/09/2026
    A partir de 4.000 pontos
    Só ida
    """

    offers = parse_rendered_offers(rendered, config())

    assert len(offers) == 1
    assert offers[0].departure_date == "14/09/2026"
    assert offers[0].points == 5800
    assert offers[0].origin == "CNF"
    assert offers[0].destination == "SLZ"
