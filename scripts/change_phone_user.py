from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from personal_news_agent.config import settings
from personal_news_agent.services.phone_verification import PhoneVerificationService, normalize_mainland_mobile
from personal_news_agent.services.store import NewsStore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview or change one PNA account mobile while preserving its password and user-owned data."
    )
    parser.add_argument("--old-mobile", required=True)
    parser.add_argument("--new-mobile", required=True)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually change the login mobile. Without this flag the command only previews.",
    )
    args = parser.parse_args()

    if not os.getenv("PERSONAL_NEWS_DB"):
        raise SystemExit(
            "PERSONAL_NEWS_DB is not set. Load the server runtime environment first: source .env.ext"
        )
    if not settings.phone_challenge_secret:
        raise SystemExit(
            "PNA_PHONE_CHALLENGE_SECRET is not configured; refusing to leave stale verification records."
        )

    old_mobile = normalize_mainland_mobile(args.old_mobile)
    new_mobile = normalize_mainland_mobile(args.new_mobile)
    if old_mobile == new_mobile:
        raise SystemExit("old and new mobile numbers are identical")

    store = NewsStore(settings.sqlite_path)
    store.init()
    verification = PhoneVerificationService(store, settings)
    try:
        result = store.change_phone_user(
            old_mobile,
            new_mobile,
            verification.mobile_hash(old_mobile),
            verification.mobile_hash(new_mobile),
            confirm=args.confirm,
        )
    except ValueError as exc:
        messages = {
            "source_mobile_not_registered": "The old mobile is not registered.",
            "target_mobile_already_registered": "The new mobile already belongs to another account.",
        }
        raise SystemExit(messages.get(str(exc), str(exc))) from exc

    result["database"] = str(settings.sqlite_path)
    result["mode"] = "change" if args.confirm else "preview"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.confirm:
        print("Preview only. Re-run with --confirm to change this account.", file=sys.stderr)


if __name__ == "__main__":
    main()
