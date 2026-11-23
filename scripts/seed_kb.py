"""Rebuild the knowledge base from the bundled sample documents.

Usage:
    python scripts/seed_kb.py [--force]

Without --force the script skips when the collection already has points.
"""

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.rag.ingest import ingest_directory  # noqa: E402
from app.rag.store import get_vector_store  # noqa: E402


async def main(force: bool) -> int:
    store = get_vector_store()
    await store.initialize()
    if not store.available:
        print("Vector store unavailable - cannot seed.")
        return 1

    count = await store.count()
    if count and not force:
        print(f"Knowledge base already has {count} chunks (use --force to rebuild).")
        return 0

    if force and count:
        # Remove all existing sample documents before rebuilding.
        for name in ("company_handbook.md", "products.md", "README.md"):
            await store.delete_document("sample:" + name)

    data_dir = ROOT / "data"
    ids = await ingest_directory(
        directory=str(data_dir),
        glob_pattern="*.md",
        doc_id_prefix="sample:",
        chunk_size=500,
        overlap=80,
    )
    print(f"Seeded {len(ids)} chunks into the knowledge base.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="rebuild even if populated")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.force)))
