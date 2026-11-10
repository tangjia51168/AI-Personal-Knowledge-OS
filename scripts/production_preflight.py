"""Check production settings without outputting secrets or contacting services."""

from app.core.config import get_settings
from app.core.production import validate_production

if __name__ == "__main__":
    settings = get_settings()
    if settings.app_env != "production":
        raise SystemExit("APP_ENV must be production for this preflight")
    validate_production(settings)
    print("PASS: production configuration checks; external release gates still require evidence")
