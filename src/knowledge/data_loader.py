import json
import re
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


TECHNIQUE_ID_PATTERN = re.compile(r"\((T\d{4}(?:\.\d{3})?)\)")
PDF_JSON_SUFFIX = "__pdf.json"


def _extract_text_from_pdf(pdf_path: Path) -> str:
    """Ekstrak teks dari file PDF untuk diproses oleh pipeline."""
    if PdfReader is None:
        print(
            "[WARN] pypdf belum terpasang. Install dulu dengan: pip install pypdf"
        )
        return ""

    try:
        reader = PdfReader(str(pdf_path))
        pages_text = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages_text.append(page_text.strip())
        return "\n\n".join(pages_text).strip()
    except Exception as exc:
        print(f"[WARN] Gagal membaca PDF {pdf_path.name}: {exc}")
        return ""


def _convert_pdf_to_json(pdf_path: Path, output_path: Path) -> bool:
    """Konversi satu file PDF menjadi JSON agar format input pipeline konsisten."""
    text = _extract_text_from_pdf(pdf_path)
    if not text:
        return False

    payload = {
        "id": pdf_path.stem,
        "source_type": "pdf",
        "source_file": pdf_path.name,
        "title": pdf_path.stem,
        "text": text,
        "techniques": [],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[PDF->JSON] {pdf_path.name} -> {output_path.name}")
    return True


def _auto_convert_pdfs_to_json(data_path: Path) -> None:
    """Konversi semua PDF ke JSON jika belum ada atau jika PDF lebih baru."""
    pdf_files = sorted(data_path.glob("*.pdf"), key=lambda p: p.name.lower())

    for pdf_file in pdf_files:
        output_path = data_path / f"{pdf_file.stem}{PDF_JSON_SUFFIX}"

        if output_path.exists() and output_path.stat().st_mtime >= pdf_file.stat().st_mtime:
            continue

        _convert_pdf_to_json(pdf_file, output_path)


def _extract_text_from_report(data: dict) -> str:
    """Ambil teks laporan dari beberapa format TRAM II yang didukung."""
    if isinstance(data.get("sentences"), list):
        text_parts = []
        for sentence in data["sentences"]:
            text = sentence.get("text", "")
            if text:
                text_parts.append(text)
        return " ".join(text_parts).strip()

    signal_text = data.get("signal")
    if isinstance(signal_text, str) and signal_text.strip():
        return signal_text.strip()

    plain_text = data.get("text")
    if isinstance(plain_text, str) and plain_text.strip():
        return plain_text.strip()

    return ""


def _extract_techniques_from_report(data: dict) -> list[str]:
    """Ekstrak label ATT&CK dari format TRAM II atau .mjson."""
    techniques: list[str] = []

    if isinstance(data.get("sentences"), list):
        for sentence in data["sentences"]:
            for mapping in sentence.get("mappings", []):
                technique_id = mapping.get("attack_id", "")
                if technique_id and technique_id not in techniques:
                    techniques.append(technique_id)

    for aset in data.get("asets", []):
        aset_type = aset.get("type", "")
        match = TECHNIQUE_ID_PATTERN.search(aset_type)
        if match:
            technique_id = match.group(1)
            if technique_id not in techniques:
                techniques.append(technique_id)

    return techniques

def load_tram_dataset(data_dir: str) -> list[dict]:
    """
    Membaca dataset TRAM II dari folder JSON.
    Setiap file berisi satu laporan CTI dengan label teknik ATT&CK.
    
    Returns:
        list of dict: [{"text": "...", "techniques": ["T1566", ...]}]
    """
    reports = []
    data_path = Path(data_dir)

    _auto_convert_pdfs_to_json(data_path)
    
    candidate_files = (
        list(data_path.glob("*.json"))
        + list(data_path.glob("*.mjson"))
    )

    for report_file in sorted(candidate_files, key=lambda p: p.name.lower()):
        try:
            with open(report_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            print(f"[WARN] Lewati file invalid JSON {report_file.name}: {exc}")
            continue

        text = _extract_text_from_report(data)
        techniques = _extract_techniques_from_report(data)
        report_id = data.get("id") or data.get("title") or report_file.stem

        if text:
            reports.append({
                "id": report_id,
                "text": text,
                "techniques": techniques
            })
    
    return reports


def split_dataset(reports: list[dict], test_ratio: float = 0.2) -> tuple:
    """
    Membagi dataset menjadi train dan test.
    """
    split_idx = int(len(reports) * (1 - test_ratio))
    train = reports[:split_idx]
    test = reports[split_idx:]
    return train, test


if __name__ == "__main__":
    # Test modul ini
    reports = load_tram_dataset("data/tram_ii")
    print(f"Total laporan: {len(reports)}")
    
    if reports:
        print(f"Contoh laporan pertama:")
        print(f"  ID: {reports[0]['id']}")
        print(f"  Panjang teks: {len(reports[0]['text'])} karakter")
        print(f"  Teknik: {reports[0]['techniques']}")