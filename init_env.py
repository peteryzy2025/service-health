"""Generate local Compose configuration once, without printing secrets."""
import base64
from pathlib import Path
import secrets


def main():
    path = Path(__file__).resolve().parent / ".env"
    values = {
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(48),
        "TOKEN_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "PROBE_ORIGINS": "http://web:8000",
        "DEMO_ENABLED": "1",
    }
    with path.open("x", encoding="ascii") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")
    print("Created .env")


if __name__ == "__main__":
    main()
