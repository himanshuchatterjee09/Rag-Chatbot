"""
CLI: Load Excel files into Azure SQL.

Usage:
  python ingestion/excel_to_sql.py \
    --portfolio   data/AIPortfolio.xlsx \
    --initiatives data/AIInitiatives.xlsx
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
        portfolio_path=args.portfolio,
        initiatives_path=args.initiatives,
    )

    for table, count in results.items():
        print(f"  ✓ {table}: {count} rows upserted")

    print(f"\nTotal tables processed: {len(results)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load Excel data into Azure SQL")
    parser.add_argument("--portfolio",    help="Path to AIPortfolio.xlsx")
    parser.add_argument("--initiatives",  help="Path to AIInitiatives.xlsx")
    parsed = parser.parse_args()

    if not any([parsed.portfolio, parsed.initiatives]):
        parser.error("Provide at least one Excel file path.")

    asyncio.run(main(parsed))
