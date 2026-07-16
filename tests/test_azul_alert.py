from src.azul_alert import Config, FlightOffer, filter_offers, parse_seats_aero_offers


def config() -> Config:
    return Config(
        origin="CNF",
        destination="SLZ",
        max_points=6000,
        search_days=365,
        email_to="teste@example.com",
        resend_api_key="re_test",
        seats_aero_api_key="seats_test",
        email_from="Alertas Azul <onboarding@resend.dev>",
    )


def test_parse_seats_aero_offers() -> None:
    payload = {
        "data": [
            {
                "Date": "2026-09-14",
                "Route": {
                    "OriginAirport": "CNF",
                    "DestinationAirport": "SLZ",
                    "Source": "azul",
                },
                "YAvailable": True,
                "YMileageCost": "5800",
                "YDirect": True,
                "YAirlines": "AD",
            }
        ]
    }

    offers = parse_seats_aero_offers(payload)

    assert len(offers) == 1
    assert offers[0].points == 5800
    assert offers[0].direct is True


def test_filter_offers_matches_rule() -> None:
    offers = [
        FlightOffer("2026-09-14", "CNF", "SLZ", 5800, True, "AD", "azul"),
        FlightOffer("2026-09-15", "CNF", "SLZ", 9000, True, "AD", "azul"),
        FlightOffer("2026-10-14", "CNF", "REC", 5000, True, "AD", "azul"),
    ]

    matches = filter_offers(offers, config())

    assert len(matches) == 1
    assert matches[0].points == 5800
