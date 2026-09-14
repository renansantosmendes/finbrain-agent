"""Register, inspect, or remove the Telegram webhook for the FinBrain bot.

The webhook is what makes Telegram push updates to POST /telegram/webhook
in app.py (see telegram.py and the README's "Bot do Telegram" section) --
this is the one-time (or one-time-per-URL-change) setup step for that.

Usage:
    python -m utils.set_telegram_webhook https://finbrain-agent.vercel.app
    python -m utils.set_telegram_webhook --info
    python -m utils.set_telegram_webhook --delete

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET from the environment
(loaded from a .env in the current directory, same as app.py) -- the same
variables that must already be set wherever the app is deployed.
"""
import argparse
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET")

WEBHOOK_PATH = "/telegram/webhook"


def _api_url(method: str) -> str:
    if not TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN não está definido (verifique o .env ou as variáveis de ambiente).")
    return f"https://api.telegram.org/bot{TOKEN}/{method}"


def _print_result(response: httpx.Response) -> None:
    print(f"HTTP {response.status_code}")
    try:
        print(response.json())
    except ValueError:
        print(response.text)


def set_webhook(base_url: str) -> None:
    url = base_url.rstrip("/") + WEBHOOK_PATH
    payload = {"url": url}
    if SECRET:
        payload["secret_token"] = SECRET
    else:
        print(
            "Aviso: TELEGRAM_WEBHOOK_SECRET não definido -- registrando o webhook sem secret_token "
            "(qualquer requisição para /telegram/webhook será aceita como se fosse do Telegram).",
            file=sys.stderr,
        )

    print(f"Registrando webhook: {url}")
    response = httpx.post(_api_url("setWebhook"), data=payload, timeout=10.0)
    _print_result(response)


def get_info() -> None:
    response = httpx.get(_api_url("getWebhookInfo"), timeout=10.0)
    _print_result(response)


def delete_webhook() -> None:
    response = httpx.post(_api_url("deleteWebhook"), timeout=10.0)
    _print_result(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", nargs="?", help="URL pública do deploy, ex: https://finbrain-agent.vercel.app")
    parser.add_argument("--info", action="store_true", help="Mostra o webhook atualmente registrado")
    parser.add_argument("--delete", action="store_true", help="Remove o webhook registrado")
    args = parser.parse_args()

    chosen = [bool(args.url), args.info, args.delete]
    if sum(chosen) != 1:
        parser.error("informe a URL do deploy OU use --info OU --delete (exatamente uma opção).")

    if args.info:
        get_info()
    elif args.delete:
        delete_webhook()
    else:
        set_webhook(args.url)


if __name__ == "__main__":
    main()
