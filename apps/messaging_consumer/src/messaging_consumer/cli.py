"""Command line entry points (Raycast can invoke these)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

import structlog

from .config import MessagingSettings
from .greetings import GreetingRequest, GreetingService

logger = structlog.get_logger(__name__)


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Messaging consumer helpers")
    subparsers = parser.add_subparsers(dest="command", required=True)

    greetings = subparsers.add_parser("greetings", help="Generate a greeting draft")
    greetings.add_argument("callsign", help="Company callsign (lowercase)")
    greetings.add_argument("recipients", help="Comma-separated recipient emails")
    greetings.add_argument(
        "--first-names",
        dest="first_names",
        required=True,
        help="How you'd like to address the recipients (e.g. 'Alex and team')",
    )
    greetings.add_argument(
        "--gift-link",
        dest="gift_link",
        default="https://mercury.com",
        help="Gift link to insert into the template",
    )
    greetings.add_argument(
        "--notes",
        dest="manual_notes",
        help="Optional manual notes to emphasize in the blurb",
    )
    greetings.add_argument(
        "--kb-text",
        dest="knowledge_text",
        help="Paste output from Unleash (will be rewritten in your voice)",
    )
    greetings.add_argument(
        "--subject",
        dest="subject",
        help="Override the default email subject",
    )
    greetings.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip Gmail draft creation and print the HTML to stdout",
    )

    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> None:
    args = _parse_args(argv or sys.argv[1:])

    if args.command == "greetings":
        _run_greetings(args)


def _run_greetings(args: argparse.Namespace) -> None:
    settings = MessagingSettings()
    service = GreetingService(settings)

    recipients = [email.strip() for email in args.recipients.split(",") if email.strip()]
    if not recipients:
        raise ValueError("Provide at least one recipient email (comma-separated)")

    request = GreetingRequest(
        callsign=args.callsign,
        recipients=recipients,
        first_names=args.first_names,
        gift_link=args.gift_link,
        manual_notes=args.manual_notes,
        knowledge_base_text=args.knowledge_text,
        subject=args.subject,
    )

    if args.dry_run:
        logger.info("Running in dry-run mode; Gmail draft will not be created")
        result = service.generate(request, create_draft=False)
        Path("greeting_preview.html").write_text(result.html, encoding="utf-8")
        print(result.html)
    else:
        service.generate(request)


if __name__ == "__main__":
    main()
