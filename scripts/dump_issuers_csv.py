#!/usr/bin/env python3
"""CLI utility to export indexed guideline records to a CSV file for manual inspection.

Uses only the standard library to maintain standard architectural compliance.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path


def dump_issuers_to_csv(db_path: str, output_path: str, issuers: list[str] | None = None) -> int:
    """Query the Store database and write matching issuer records directly to a CSV file."""
    db_file = Path(db_path)
    if not db_file.exists():
        print(f"Error: Database file not found at '{db_path}'", file=sys.stderr)
        return 1

    # Open standard connection to read rows from the guidelines table
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        if issuers:
            # Prepare an uppercase tuple list matching against issuer_abbrev column keys
            placeholders = ",".join("?" for _ in issuers)
            query = f"SELECT * FROM guidelines WHERE UPPER(issuer_abbrev) IN ({placeholders}) ORDER BY issuer_abbrev, title"
            params = [i.upper() for i in issuers]
            cursor.execute(query, params)
        else:
            query = "SELECT * FROM guidelines ORDER BY issuer_abbrev, title"
            cursor.execute(query)

        rows = cursor.fetchall()
        if not rows:
            print("No records found matching the specified parameters.", file=sys.stderr)
            return 0

        # Extract column header headers cleanly from the row keys
        fields = list(rows[0].keys())

        # Safely write spreadsheet metadata rows out to disk
        with open(output_path, mode="w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))

        print(f"🟢 Successfully exported {len(rows)} rows to '{output_path}'")
        return 0

    except Exception as exc:
        print(f"❌ Failed to execute database export operation: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def main() -> int:
    """Command-line interface loop driver configuration."""
    ap = argparse.ArgumentParser(description="Export MGI index records to CSV format.")
    ap.add_argument("--db", default="mgi.db", help="Path to the source SQLite database (default: mgi.db)")
    ap.add_argument("--out", default="indexed_guidelines.csv", help="Path for the output file")
    ap.add_argument("--issuer", action="append", dest="issuers", help="Issuer code to export (repeatable)")
    args = ap.parse_args()

    return dump_issuers_to_csv(args.db, args.out, args.issuers)


if __name__ == "__main__":
    sys.exit(main())