import argparse
import json
from pathlib import Path

from pypdf import PdfReader


PDF_JSON_SUFFIX = "__pdf.json"


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract plain text from all pages in a PDF file."""
    reader = PdfReader(str(pdf_path))
    pages_text = []

    for page in reader.pages:
        page_text = page.extract_text() or ""
        if page_text.strip():
            pages_text.append(page_text.strip())

    return "\n\n".join(pages_text).strip()


def convert_pdf_to_json(pdf_path: Path, output_path: Path) -> bool:
    """Convert one PDF report into JSON format compatible with data_loader."""
    try:
        text = extract_text_from_pdf(pdf_path)
        if not text:
            print(f"[SKIP] {pdf_path.name}: no extractable text")
            return False

        payload = {
            "source_type": "pdf",
            "source_file": pdf_path.name,
            "title": pdf_path.stem,
            "text": text,
            "techniques": [],
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

        print(f"[OK] {pdf_path.name} -> {output_path.name}")
        return True
    except Exception as exc:
        print(f"[ERROR] {pdf_path.name}: {exc}")
        return False


def find_pdf_files(input_dir: Path, recursive: bool) -> list[Path]:
    """Collect PDF files from input directory."""
    pattern = "**/*.pdf" if recursive else "*.pdf"
    return sorted(input_dir.glob(pattern), key=lambda p: p.name.lower())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert CTI PDF reports into JSON files for TTP mapping pipeline."
    )
    parser.add_argument(
        "--input-dir",
        default="data/tram_ii",
        help="Directory that contains PDF files (default: data/tram_ii)",
    )
    parser.add_argument(
        "--output-dir",
        default="data/tram_ii",
        help="Directory to write converted JSON files (default: data/tram_ii)",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Search PDFs recursively inside input-dir",
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="Optional filename prefix for output JSON files",
    )
    parser.add_argument(
        "--suffix",
        default=PDF_JSON_SUFFIX,
        help="Output filename suffix before .json (default: __pdf.json)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    if not input_dir.exists():
        raise SystemExit(f"Input directory not found: {input_dir}")

    pdf_files = find_pdf_files(input_dir, recursive=args.recursive)
    if not pdf_files:
        print(f"No PDF files found in: {input_dir}")
        return

    converted = 0
    for pdf_file in pdf_files:
        output_name = f"{args.prefix}{pdf_file.stem}{args.suffix}"
        if not output_name.lower().endswith(".json"):
            output_name = f"{output_name}.json"
        output_path = output_dir / output_name
        if convert_pdf_to_json(pdf_file, output_path):
            converted += 1

    print("-" * 60)
    print(f"Total PDF found : {len(pdf_files)}")
    print(f"Converted       : {converted}")
    print(f"Failed/Skipped  : {len(pdf_files) - converted}")


if __name__ == "__main__":
    main()
