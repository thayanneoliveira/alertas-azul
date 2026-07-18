"""Monitor pessoal de passagens Azul por pontos com Playwright.

O robô abre a página pública de ofertas em pontos da Azul, aplica origem,
destino e orçamento máximo e extrai os cards renderizados no navegador.
Não realiza login, reserva ou emissão automática.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests
from dotenv import load_dotenv
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

load_dotenv()

AZUL_POINTS_URL = "https://passagens.voeazul.com.br/pt/pontos"
RESEND_API_URL = "https://api.resend.com/emails"
HISTORY_FILE = Path("alert_history.json")
ARTIFACT_DIR = Path("artifacts")


@dataclass(frozen=True)
class Config:
    origin: str
    destination: str
    max_points: int
    email_to: str
    email_from: str
    resend_api_key: str

    @classmethod
    def from_env(cls) -> "Config":
        required = ("ORIGIN", "DESTINATION", "MAX_POINTS", "EMAIL_TO", "RESEND_API_KEY")
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise ValueError(f"Variáveis ausentes: {', '.join(missing)}")

        max_points = int(os.environ["MAX_POINTS"])
        if max_points <= 0:
            raise ValueError("MAX_POINTS deve ser maior que zero")

        return cls(
            origin=os.environ["ORIGIN"].strip().upper(),
            destination=os.environ["DESTINATION"].strip().upper(),
            max_points=max_points,
            email_to=os.environ["EMAIL_TO"].strip(),
            email_from=os.getenv(
                "EMAIL_FROM", "Alertas Azul <onboarding@resend.dev>"
            ).strip(),
            resend_api_key=os.environ["RESEND_API_KEY"].strip(),
        )


@dataclass(frozen=True)
class FlightOffer:
    departure_date: str
    origin: str
    destination: str
    points: int
    trip_type: str
    url: str

    @property
    def key(self) -> str:
        return (
            f"{self.departure_date}|{self.origin}|{self.destination}|"
            f"{self.points}|{self.trip_type}"
        )


def normalize_points(value: str) -> int:
    digits = re.sub(r"\D", "", value)
    if not digits:
        raise ValueError("Pontuação inválida")
    return int(digits)


def _fill_search_field(page: Page, index: int, value: str) -> None:
    inputs = page.get_by_placeholder("Digitar origem")
    if index == 1:
        inputs = page.get_by_placeholder("Digitar destino")

    locator = inputs.first
    locator.wait_for(state="visible", timeout=20_000)
    locator.click()
    locator.fill(value)
    page.wait_for_timeout(1_500)
    locator.press("ArrowDown")
    locator.press("Enter")


def _fill_budget(page: Page, max_points: int) -> None:
    budget = page.get_by_placeholder("Digite o orçamento máximo").first
    budget.wait_for(state="visible", timeout=20_000)
    budget.fill(str(max_points))
    budget.press("Enter")


def _apply_filters(page: Page, config: Config) -> None:
    _fill_search_field(page, 0, config.origin)
    _fill_search_field(page, 1, config.destination)
    _fill_budget(page, config.max_points)

    # Algumas versões da página aplicam filtros automaticamente; outras expõem
    # um botão de busca. O clique é tentado sem tornar o fluxo dependente dele.
    for label in ("Buscar", "Pesquisar", "Aplicar filtros"):
        button = page.get_by_role("button", name=re.compile(label, re.I))
        if button.count() and button.first.is_visible():
            button.first.click()
            break

    page.wait_for_timeout(6_000)


def parse_rendered_offers(text: str, config: Config) -> list[FlightOffer]:
    """Extrai cards da vitrine pública a partir do texto renderizado.

    A página costuma apresentar blocos como:
    "Belo Horizonte (CNF) Para São Luís (SLZ) Ida: 14/09/2026
    A partir de 5.800 pontos Só ida".
    """
    compact = re.sub(r"\s+", " ", text)
    pattern = re.compile(
        r"(?P<origin_name>.{0,80}?)\((?P<origin>[A-Z]{3})\)\s*Para\s*"
        r"(?P<destination_name>.{0,80}?)\((?P<destination>[A-Z]{3})\)\s*"
        r"(?:Ida|Partida):\s*(?P<date>\d{1,2}/\d{1,2}/\d{4}).{0,120}?"
        r"A partir de\s*(?P<points>[\d\.,]+)\s*pontos?.{0,80}?"
        r"(?P<trip>Só ida|Ida e volta)",
        re.IGNORECASE,
    )

    offers: list[FlightOffer] = []
    for match in pattern.finditer(compact):
        origin = match.group("origin").upper()
        destination = match.group("destination").upper()
        points = normalize_points(match.group("points"))
        if origin != config.origin or destination != config.destination:
            continue
        if points > config.max_points:
            continue
        offers.append(
            FlightOffer(
                departure_date=match.group("date"),
                origin=origin,
                destination=destination,
                points=points,
                trip_type=match.group("trip"),
                url=AZUL_POINTS_URL,
            )
        )

    return sorted(offers, key=lambda item: (item.points, item.departure_date))


def fetch_offers(config: Config) -> list[FlightOffer]:
    ARTIFACT_DIR.mkdir(exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            response = page.goto(AZUL_POINTS_URL, wait_until="domcontentloaded", timeout=60_000)
            if response is not None and response.status >= 400:
                raise RuntimeError(f"Azul respondeu HTTP {response.status}")

            page.wait_for_timeout(5_000)
            body_before = page.locator("body").inner_text(timeout=20_000)
            blocked_markers = ("Access denied", "Forbidden", "Verifique se você é humano")
            if any(marker.lower() in body_before.lower() for marker in blocked_markers):
                raise RuntimeError("A proteção do site bloqueou o navegador automatizado")

            _apply_filters(page, config)
            rendered_text = page.locator("body").inner_text(timeout=20_000)
            page.screenshot(path=str(ARTIFACT_DIR / "azul-resultados.png"), full_page=True)
            (ARTIFACT_DIR / "azul-resultados.txt").write_text(
                rendered_text, encoding="utf-8"
            )

            offers = parse_rendered_offers(rendered_text, config)
            print(
                f"Página Azul consultada com navegador. "
                f"Rota {config.origin} → {config.destination}. "
                f"Ofertas até {config.max_points} pontos: {len(offers)}."
            )
            return offers
        except Exception:
            try:
                page.screenshot(path=str(ARTIFACT_DIR / "azul-erro.png"), full_page=True)
                (ARTIFACT_DIR / "azul-erro.txt").write_text(
                    page.locator("body").inner_text(timeout=5_000), encoding="utf-8"
                )
            except Exception:
                pass
            raise
        finally:
            context.close()
            browser.close()


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
        f"{offer.origin} → {offer.destination}: "
        f"<strong>{offer.points:,} pontos</strong> — "
        f"{html.escape(offer.trip_type)} "
        f"(<a href='{html.escape(offer.url)}'>abrir Azul</a>)"
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

    try:
        offers = fetch_offers(config)
    except (RuntimeError, PlaywrightTimeoutError) as exc:
        print(f"Falha ao consultar a Azul pelo navegador: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Erro inesperado ao consultar a Azul: {exc}", file=sys.stderr)
        return 1

    history = load_history()
    new_matches = [offer for offer in offers if offer.key not in history]

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
