"""
CLI: Load Excel files into Azure SQL.

Usage:
  python ingestion/excel_to_sql.py \
    --company  data/company_profile.xlsx \
    --initiatives data/ai_initiatives.xlsx \
    --adoption data/ai_adoption_index.xlsx
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / "backend" / ".env")

from app.config import get_settings
from app.services.excel_ingestion import ExcelIngestionService


async def main(args: argparse.Namespace):
    settings = get_settings()
    svc = ExcelIngestionService(settings)

    results = await svc.ingest_all(
        company_profile_path=args.company,
        ai_initiatives_path=args.initiatives,
        ai_adoption_path=args.adoption,
    )

    for table, count in results.items():
        print(f"  ✓ {table}: {count} rows upserted")

    print(f"\nTotal tables processed: {len(results)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load Excel data into Azure SQL")
    parser.add_argument("--company",      help="Path to company_profile.xlsx")
    parser.add_argument("--initiatives",  help="Path to ai_initiatives.xlsx")
    parser.add_argument("--adoption",     help="Path to ai_adoption_index.xlsx")
    parsed = parser.parse_args()

    if not any([parsed.company, parsed.initiatives, parsed.adoption]):
        parser.error("Provide at least one Excel file path.")

    asyncio.run(main(parsed))
