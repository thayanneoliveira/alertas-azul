"""Monitor pessoal de passagens Azul por pontos usando a API do Seats.aero."""

from __future__ import annotations

import html
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import requests
from dotenv import load_dotenv

load_dotenv()

SEATS_AERO_SEARCH_URL = "https://seats.aero/partnerapi/search"
RESEND_API_URL = "https://api.resend.com/emails"
HISTORY_FILE = Path("alert_history.json")


@dataclass(frozen=True)
class Config:
    origin: str
    destination: str
    max_points: int
    search_days: int
    email_to: str
    email_from: str
    resend_api_key: str
    seats_aero_api_key: str

    @classmethod
    def from_env(cls) -> "Config":
        required = (
            "ORIGIN",
            "DESTINATION",
            "MAX_POINTS",
            "EMAIL_TO",
            "RESEND_API_KEY",
            "SEATS_AERO_API_KEY",
        )
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise ValueError(f"Variáveis ausentes: {', '.join(missing)}")

        max_points = int(os.environ["MAX_POINTS"])
        search_days = int(os.getenv("SEARCH_DAYS", "365"))
        if max_points <= 0:
            raise ValueError("MAX_POINTS deve ser maior que zero")
        if not 1 <= search_days <= 365:
            raise ValueError("SEARCH_DAYS deve estar entre 1 e 365")

        return cls(
            origin=os.environ["ORIGIN"].strip().upper(),
            destination=os.environ["DESTINATION"].strip().upper(),
            max_points=max_points,
            search_days=search_days,
            email_to=os.environ["EMAIL_TO"].strip(),
            email_from=os.getenv(
                "EMAIL_FROM", "Alertas Azul <onboarding@resend.dev>"
            ).strip(),
            resend_api_key=os.environ["RESEND_API_KEY"].strip(),
            seats_aero_api_key=os.environ["SEATS_AERO_API_KEY"].strip(),
        )


@dataclass(frozen=True)
class FlightOffer:
    departure_date: str
    origin: str
    destination: str
    points: int
    direct: bool
    airlines: str
    source: str

    @property
    def key(self) -> str:
        return (
            f"{self.departure_date}|{self.origin}|{self.destination}|"
            f"{self.points}|{self.direct}|{self.airlines}|{self.source}"
        )


def _first(mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping and mapping[name] not in (None, ""):
            return mapping[name]
    return None


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _iter_availability_objects(payload: Any) -> Iterable[dict[str, Any]]:
    """Aceita pequenas variações no envelope JSON da API."""
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return

    if not isinstance(payload, dict):
        return

    for key in ("data", "availability", "availabilities", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item
            return

    # Em último caso, considera o próprio objeto uma disponibilidade.
    yield payload


def parse_seats_aero_offers(payload: Any) -> list[FlightOffer]:
    offers: list[FlightOffer] = []

    for item in _iter_availability_objects(payload):
        route = item.get("Route") or item.get("route") or {}
        if not isinstance(route, dict):
            route = {}

        origin = _first(
            item, "OriginAirport", "origin_airport", "originAirport", "origin"
        ) or _first(route, "OriginAirport", "origin_airport", "originAirport", "origin")
        destination = _first(
            item,
            "DestinationAirport",
            "destination_airport",
            "destinationAirport",
            "destination",
        ) or _first(
            route,
            "DestinationAirport",
            "destination_airport",
            "destinationAirport",
            "destination",
        )
        departure_date = _first(item, "Date", "date", "departure_date", "departureDate")
        points = _to_int(
            _first(
                item,
                "YMileageCost",
                "y_mileage_cost",
                "economy_mileage_cost",
                "economyMileageCost",
            )
        )
        available = _first(item, "YAvailable", "y_available", "economy_available")
        direct = bool(_first(item, "YDirect", "y_direct", "economy_direct") or False)
        airlines = str(
            _first(item, "YAirlines", "y_airlines", "economy_airlines") or "Azul"
        )
        source = str(_first(item, "Source", "source") or _first(route, "Source", "source") or "azul")

        if available is False:
            continue
        if not origin or not destination or not departure_date or points is None:
            continue

        offers.append(
            FlightOffer(
                departure_date=str(departure_date)[:10],
                origin=str(origin).upper(),
                destination=str(destination).upper(),
                points=points,
                direct=direct,
                airlines=airlines,
                source=source,
            )
        )

    return offers


def fetch_offers(config: Config) -> list[FlightOffer]:
    start = date.today()
    end = start + timedelta(days=config.search_days)

    response = requests.get(
        SEATS_AERO_SEARCH_URL,
        headers={
            "Partner-Authorization": config.seats_aero_api_key,
            "Accept": "application/json",
            "User-Agent": "alertas-azul/1.0",
        },
        params={
            "origin_airport": config.origin,
            "destination_airport": config.destination,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "sources": "azul",
            "cabins": "economy",
            "order_by": "lowest_mileage",
            "take": 1000,
            "include_filtered": "true",
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    offers = parse_seats_aero_offers(payload)
    print(
        f"Seats.aero consultado: {config.origin} → {config.destination}, "
        f"{start.isoformat()} até {end.isoformat()}. Ofertas extraídas: {len(offers)}."
    )
    return offers


def filter_offers(offers: Iterable[FlightOffer], config: Config) -> list[FlightOffer]:
    return sorted(
        (
            offer
            for offer in offers
            if offer.origin == config.origin
            and offer.destination == config.destination
            and offer.points <= config.max_points
        ),
        key=lambda offer: (offer.points, offer.departure_date),
    )


def load_history() -> set[str]:
    if not HISTORY_FILE.exists():
        return set()
    try:
        return set(json.loads(HISTORY_FILE.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return set()


def save_history(keys: set[str]) -> None:
    HISTORY_FILE.write_text(
        json.dumps(sorted(keys), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def send_email(config: Config, offers: list[FlightOffer]) -> None:
    subject = (
        f"Alerta Azul — {config.origin} → {config.destination} "
        f"até {config.max_points:,} pontos"
    ).replace(",", ".")

    rows = "".join(
        "<li>"
        f"<strong>{html.escape(offer.departure_date)}</strong> — "
        f"{html.escape(offer.origin)} → {html.escape(offer.destination)}: "
        f"<strong>{offer.points:,} pontos</strong>"
        f"{' — direto' if offer.direct else ''}"
        "</li>".replace(",", ".")
        for offer in offers
    )

    body = f"""
    <h2>Encontramos uma possível oportunidade</h2>
    <ul>{rows}</ul>
    <p>Fonte de disponibilidade: Seats.aero / Azul Fidelidade.</p>
    <p>Confirme o valor, as taxas e a disponibilidade no site ou app oficial da Azul antes de emitir.</p>
    """

    response = requests.post(
        RESEND_API_URL,
        headers={
            "Authorization": f"Bearer {config.resend_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": config.email_from,
            "to": [config.email_to],
            "subject": subject,
            "html": body,
        },
        timeout=30,
    )
    response.raise_for_status()


def main() -> int:
    try:
        config = Config.from_env()
    except (ValueError, TypeError) as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 2

    try:
        offers = fetch_offers(config)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "desconhecido"
        body = exc.response.text[:500] if exc.response is not None else ""
        print(
            f"Falha na API Seats.aero (HTTP {status}). {body}",
            file=sys.stderr,
        )
        return 1
    except (requests.RequestException, ValueError) as exc:
        print(f"Falha ao consultar a API Seats.aero: {exc}", file=sys.stderr)
        return 1

    matches = filter_offers(offers, config)
    print(f"Ofertas dentro do limite de {config.max_points} pontos: {len(matches)}.")

    history = load_history()
    new_matches = [offer for offer in matches if offer.key not in history]

    if not new_matches:
        print("Nenhuma oferta nova encontrada dentro dos critérios.")
        return 0

    try:
        send_email(config, new_matches)
    except requests.RequestException as exc:
        print(f"Falha ao enviar e-mail pelo Resend: {exc}", file=sys.stderr)
        return 1

    history.update(offer.key for offer in new_matches)
    save_history(history)
    print(f"Alerta enviado para {len(new_matches)} oferta(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
