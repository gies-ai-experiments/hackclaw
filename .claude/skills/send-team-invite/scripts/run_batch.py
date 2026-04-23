"""Batch driver for stand_up_team.py.

Reads a CSV with columns `name,email,team` and runs the single-participant
flow for each row, with a short sleep between rows to stay well under
Discord REST rate limits.

Usage:
    python3 run_batch.py roster.csv [--dry-run]

Skips rows where:
- name, email, or team is blank
- email is clearly malformed (no '@')
- the same (email, team) pair has already been processed this run

Prints a JSON summary at the end so you can spot-check results.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

# stand_up_team lives next to us on the Hetzner box (scp'd into /tmp)
sys.path.insert(0, str(Path(__file__).parent))
from stand_up_team import stand_up_team  # noqa: E402


def _valid_email(s: str) -> bool:
    return "@" in s and "." in s.split("@")[-1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", help="Path to CSV with columns name,email,team")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Seconds between participants (default: 1.0)",
    )
    args = ap.parse_args()

    results: list[dict] = []
    skipped: list[dict] = []
    seen: set[tuple[str, str]] = set()

    with open(args.csv_path) as f:
        reader = csv.DictReader(f)
        for row_num, row in enumerate(reader, start=2):
            name = (row.get("name") or "").strip()
            email = (row.get("email") or "").strip().lower()
            team = (row.get("team") or "").strip()
            key = (email, team)

            if not name or not email or not team:
                skipped.append({"row": row_num, "reason": "missing field", **row})
                continue
            if not _valid_email(email):
                skipped.append({"row": row_num, "reason": "bad email", **row})
                continue
            if key in seen:
                skipped.append({"row": row_num, "reason": "duplicate", **row})
                continue
            seen.add(key)

            try:
                r = stand_up_team(name=name, email=email, team=team, dry_run=args.dry_run)
                results.append(r)
                print(
                    f"✓ row {row_num}: {email} -> {r['team']} "
                    f"(role {r['role_status']}, chan {r['channel_status']}) {r['invite_url']}"
                )
            except Exception as e:
                results.append({
                    "row": row_num,
                    "name": name, "email": email, "team": team,
                    "error": str(e),
                })
                print(f"✗ row {row_num}: {email} -> {e}", file=sys.stderr)

            time.sleep(args.sleep)

    print("\n--- SUMMARY ---")
    print(json.dumps(
        {"processed": len(results), "skipped": len(skipped), "skipped_detail": skipped},
        indent=2,
    ))


if __name__ == "__main__":
    main()
