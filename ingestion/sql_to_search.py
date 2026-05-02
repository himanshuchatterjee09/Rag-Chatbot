"""
CLI: Sync Azure SQL tables → Azure AI Search index.

Usage:
  python ingestion/sql_to_search.py [--table all|ai_initiatives|ai_adoption_index|company_profile] [--recreate]
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / "backend" / ".env")

from app.config import get_settings
from app.services.search_service import SearchService
from app.services.sql_service import SQLService


async def main(args: argparse.Namespace):
    settings = get_settings()
    sql = SQLService(settings)
    search = SearchService(settings)

    print(f"Syncing table='{args.table}' to Azure AI Search (recreate={args.recreate})...")
    count = await search.index_from_sql(sql, table=args.table, recreate=args.recreate)
    print(f"  ✓ {count} documents indexed into '{settings.azure_search_index_name}'")

    await sql.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync SQL to Azure AI Search")
    parser.add_argument(
        "--table",
        default="all",
        choices=["all", "ai_initiatives", "ai_adoption_index", "company_profile"],
    )
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate the index")
    asyncio.run(main(parser.parse_args()))
