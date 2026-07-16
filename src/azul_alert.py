"""Monitor pessoal de ofertas públicas da Azul em pontos."""

from __future__ import annotations

import html
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

AZUL_VITRINE_URLS = (
    "https://www.voeazul.com.br/pt/vitrine/ate-5-mil-pontos",
    "https://www.voeazul.com.br/pt/vitrine/5-mil-10-mil-pontos",
    "https://www.voeazul.com.br/pt/ofertas",
)

RESEND_API_URL = "https://api.resend.com/emails"
HISTORY_FILE = Path("alert_history.json")


@dataclass(frozen=True)
class Config:
    origin: str
    destination: str
    max_points: int
    email_to: str
    resend_api_key: str
    email_from: str
    year: int | None = None
    month: int | None = None

    @classmethod
    def from_env(cls) -> "Config":
        required = [
            "ORIGIN",
            "DESTINATION",
            "MAX_POINTS",
            "EMAIL_TO",
            "RESEND_API_KEY",
        ]
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise ValueError(f"Variáveis ausentes: {', '.join(missing)}")

        month_raw = os.getenv("MONTH", "").strip()
        year_raw = os.getenv("YEAR", "").strip()

        month = int(month_raw) if month_raw else None
        year = int(year_raw) if year_raw else None

        if month is not None and not 1 <= month <= 12:
            raise ValueError("MONTH deve estar entre 1 e 12")

        max_points = int(os.environ["MAX_POINTS"])
        if max_points <= 0:
            raise ValueError("MAX_POINTS deve ser maior que zero")

        return cls(
            origin=os.environ["ORIGIN"].strip().upper(),
            destination=os.environ["DESTINATION"].strip().upper(),
            max_points=max_points,
            email_to=os.environ["EMAIL_TO"].strip(),
            resend_api_key=os.environ["RESEND_API_KEY"].strip(),
            email_from=os.getenv(
                "EMAIL_FROM", "Alertas Azul <onboarding@resend.dev>"
            ).strip(),
            year=year,
            month=month,
        )


@dataclass(frozen=True)
class FlightOffer:
    date: str
    origin: str
    destination: str
    points: int
    cabin: str
    url: str

    @property
    def key(self) -> str:
        return f"{self.date}|{self.origin}|{self.destination}|{self.points}|{self.url}"


def fetch_page(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/125 Safari/537.36"
        )
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.text


def normalize_points(value: str) -> int:
    digits = re.sub(r"\D", "", value)
    if not digits:
        raise ValueError("Pontuação inválida")
    return int(digits)


def parse_offers(page_html: str, source_url: str) -> list[FlightOffer]:
    """Extrai ofertas de cards públicos."""
    soup = BeautifulSoup(page_html, "html.parser")
    offers: list[FlightOffer] = []

    candidate_cards = soup.select(
        "article, [class*='offer'], [class*='card'], [data-testid*='offer']"
    )

    for card in candidate_cards:
        text = " ".join(card.stripped_strings)
        route = re.search(r"\b([A-Z]{3})\b\s*(?:→|-|para)\s*\b([A-Z]{3})\b", text)
        points_match = re.search(r"([\d\.]+)\s*pontos?", text, re.IGNORECASE)
        date_match = re.search(r"\b(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b", text)

        if not (route and points_match and date_match):
            continue

        link = card.select_one("a[href]")
        offers.append(
            FlightOffer(
                date=date_match.group(1),
                origin=route.group(1),
                destination=route.group(2),
                points=normalize_points(points_match.group(1)),
                cabin="Econômica" if "econ" in text.lower() else "Não informada",
                url=urljoin(source_url, link["href"]) if link else source_url,
            )
        )

    return offers


def parse_offer_date(value: str, fallback_year: int) -> datetime:
    parts = [int(part) for part in value.split("/")]
    if len(parts) == 2:
        day, month = parts
        year = fallback_year
    elif len(parts) == 3:
        day, month, year = parts
        if year < 100:
            year += 2000
    else:
        raise ValueError(f"Data não reconhecida: {value}")
    return datetime(year, month, day)


def filter_offers(offers: Iterable[FlightOffer], config: Config) -> list[FlightOffer]:
    matches: list[FlightOffer] = []
    current_year = datetime.now().year

    for offer in offers:
        try:
            date = parse_offer_date(offer.date, config.year or current_year)
        except ValueError:
            continue

        if offer.origin != config.origin or offer.destination != config.destination:
            continue
        if offer.points > config.max_points:
            continue
        if config.year is not None and date.year != config.year:
            continue
        if config.month is not None and date.month != config.month:
            continue

        matches.append(offer)

    return matches


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
        f"<strong>{html.escape(offer.date)}</strong> — "
        f"{html.escape(offer.origin)} → {html.escape(offer.destination)}: "
        f"<strong>{offer.points:,} pontos</strong> "
        f"(<a href='{html.escape(offer.url, quote=True)}'>ver oferta</a>)"
        "</li>".replace(",", ".")
        for offer in offers
    )

    body = f"""
    <h2>Encontramos uma possível oportunidade</h2>
    <ul>{rows}</ul>
    <p>Confirme o preço, as taxas e a disponibilidade no site ou app oficial da Azul antes de emitir.</p>
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

    all_offers: list[FlightOffer] = []
    for url in AZUL_VITRINE_URLS:
        try:
            all_offers.extend(parse_offers(fetch_page(url), url))
        except requests.RequestException as exc:
            print(f"Falha ao consultar {url}: {exc}", file=sys.stderr)

    matches = filter_offers(all_offers, config)
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
