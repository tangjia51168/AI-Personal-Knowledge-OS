"""Read account model IDs from the fixed official API without printing credentials."""

import argparse
import json
import re
from pathlib import Path

import httpx
from dotenv import dotenv_values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy", help="Explicit existing operator proxy, if required")
    args = parser.parse_args()
    values = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    key = values.get("OPENAI_API_KEY", "")
    if not key or key.startswith("你的"):
        raise SystemExit("OPENAI_API_KEY is not configured; no request sent")
    try:
        with httpx.Client(
            timeout=20, trust_env=False, follow_redirects=False, proxy=args.proxy
        ) as client:
            response = client.get(
                "https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"}
            )
            if response.status_code != 200:
                try:
                    error = response.json().get("error", {})
                except ValueError:
                    error = {}
                known_codes = {
                    "invalid_api_key",
                    "insufficient_quota",
                    "rate_limit_exceeded",
                    "unsupported_country_region_territory",
                    "access_terminated",
                }
                code = error.get("code")
                print(
                    json.dumps(
                        {
                            "status": response.status_code,
                            "models_verified": False,
                            "error_code": code if code in known_codes else "unclassified",
                        }
                    )
                )
                raise SystemExit(1)
            data = response.json()
            ids = sorted(
                row["id"]
                for row in data.get("data", [])
                if isinstance(row, dict)
                and isinstance(row.get("id"), str)
                and re.fullmatch(r"[a-zA-Z0-9._:/-]{1,120}", row["id"])
                and any(name in row["id"].lower() for name in ("gpt", "codex"))
            )
            print(json.dumps({"status": 200, "model_ids": ids}, indent=2))
    except (httpx.HTTPError, ValueError, UnicodeError) as exc:
        raise SystemExit(
            f"Official model discovery failed ({type(exc).__name__}); key not displayed"
        ) from None


if __name__ == "__main__":
    main()
