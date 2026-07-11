"""Monitor pessoal de ofertas públicas da Azul em pontos."""

from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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

HISTORY_FILE = Path("alert_history.json")


@dataclass(frozen=True)
class Config:
    origin: str
    destination: str
    year: int
    month: int
    max_points: int
    email_to: str
    smtp_server: str
    smtp_port: int
    smtp_user: str
    smtp_password: str

    @classmethod
    def from_env(cls) -> "Config":
        required = [
            "ORIGIN",
            "DESTINATION",
            "YEAR",
            "MONTH",
            "MAX_POINTS",
            "EMAIL_TO",
            "SMTP_SERVER",
            "SMTP_PORT",
            "SMTP_USER",
            "SMTP_PASSWORD",
        ]
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise ValueError(f"Variáveis ausentes: {', '.join(missing)}")

        return cls(
            origin=os.environ["ORIGIN"].upper(),
            destination=os.environ["DESTINATION"].upper(),
            year=int(os.environ["YEAR"]),
            month=int(os.environ["MONTH"]),
            max_points=int(os.environ["MAX_POINTS"]),
            email_to=os.environ["EMAIL_TO"],
            smtp_server=os.environ["SMTP_SERVER"],
            smtp_port=int(os.environ["SMTP_PORT"]),
            smtp_user=os.environ["SMTP_USER"],
            smtp_password=os.environ["SMTP_PASSWORD"],
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


def parse_offers(html: str, source_url: str) -> list[FlightOffer]:
    """Extrai ofertas de cards públicos.

    Os seletores abaixo são deliberadamente flexíveis, mas podem precisar de
    ajuste se a Azul alterar o HTML da página.
    """
    soup = BeautifulSoup(html, "html.parser")
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


def parse_offer_date(value: str, target_year: int) -> datetime:
    parts = [int(part) for part in value.split("/")]
    if len(parts) == 2:
        day, month = parts
        year = target_year
    elif len(parts) == 3:
        day, month, year = parts
        if year < 100:
            year += 2000
    else:
        raise ValueError(f"Data não reconhecida: {value}")
    return datetime(year, month, day)


def filter_offers(offers: Iterable[FlightOffer], config: Config) -> list[FlightOffer]:
    matches: list[FlightOffer] = []
    for offer in offers:
        try:
            date = parse_offer_date(offer.date, config.year)
        except ValueError:
            continue

        if (
            offer.origin == config.origin
            and offer.destination == config.destination
            and date.year == config.year
            and date.month == config.month
            and offer.points <= config.max_points
        ):
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
        f"<li><strong>{offer.date}</strong> — {offer.origin} → "
        f"{offer.destination}: <strong>{offer.points:,} pontos</strong> "
        f"(<a href='{offer.url}'>ver oferta</a>)</li>".replace(",", ".")
        for offer in offers
    )

    body = f"""
    <h2>Encontramos uma possível oportunidade</h2>
    <ul>{rows}</ul>
    <p>Confirme o preço, taxas e disponibilidade no site ou app oficial da Azul antes de emitir.</p>
    """

    message = MIMEMultipart("alternative")
    message["From"] = config.smtp_user
    message["To"] = config.email_to
    message["Subject"] = subject
    message.attach(MIMEText(body, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(
        config.smtp_server, config.smtp_port, context=context
    ) as server:
        server.login(config.smtp_user, config.smtp_password)
        server.sendmail(config.smtp_user, [config.email_to], message.as_string())


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

    send_email(config, new_matches)
    history.update(offer.key for offer in new_matches)
    save_history(history)
    print(f"Alerta enviado para {len(new_matches)} oferta(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
