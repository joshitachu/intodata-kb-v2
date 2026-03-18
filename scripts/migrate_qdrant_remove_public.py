#!/usr/bin/env python3
"""One-time migration: remove "public" from allowed_groups on all Qdrant vectors.

After this migration, documents are only accessible via their project_id tag.
Admins bypass the filter entirely, so they still see everything.

Usage:
    # Dry run (default) — shows what would change
    python scripts/migrate_qdrant_remove_public.py

    # Actually apply changes
    python scripts/migrate_qdrant_remove_public.py --apply

Environment variables:
    QDRANT_URL       Qdrant server URL (default: http://localhost:6333)
    QDRANT_API_KEY   Optional API key for Qdrant Cloud
"""
from __future__ import annotations

import argparse
import os
import sys

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, SetPayloadOperation, SetPayload, PointIdsList

COLLECTION = "documents"


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove 'public' from Qdrant allowed_groups")
    parser.add_argument("--apply", action="store_true", help="Actually apply changes (default is dry run)")
    args = parser.parse_args()

    url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    api_key = os.environ.get("QDRANT_API_KEY", "") or None

    client = QdrantClient(url=url, api_key=api_key)

    # Verify collection exists
    try:
        info = client.get_collection(COLLECTION)
        total_points = info.points_count
        print(f"Collection '{COLLECTION}': {total_points} points")
    except Exception as e:
        print(f"Error: Could not access collection '{COLLECTION}': {e}")
        sys.exit(1)

    # Scroll through all points
    updated = 0
    skipped = 0
    offset = None

    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION,
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )

        if not points:
            break

        for point in points:
            groups = point.payload.get("allowed_groups", [])
            if "public" not in groups:
                skipped += 1
                continue

            new_groups = [g for g in groups if g != "public"]
            # If removing "public" leaves an empty list, keep it empty
            # (only admins will see untagged docs)

            if args.apply:
                client.set_payload(
                    collection_name=COLLECTION,
                    payload={"allowed_groups": new_groups},
                    points=[point.id],
                )

            action = "Updated" if args.apply else "Would update"
            print(f"  {action} point {point.id}: {groups} -> {new_groups}")
            updated += 1

        if offset is None:
            break

    mode = "APPLIED" if args.apply else "DRY RUN"
    print(f"\n[{mode}] {updated} points updated, {skipped} points unchanged")

    if not args.apply and updated > 0:
        print("Run with --apply to make changes.")


if __name__ == "__main__":
    main()
