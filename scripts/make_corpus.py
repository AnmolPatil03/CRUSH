"""Write the synthetic part of the benchmark corpus into corpus/ (dev tool)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests import fixtures as fx  # noqa: E402

root = Path(__file__).resolve().parent.parent / "corpus"
(root / "txt" / "synthetic-fixed-width-table.txt").write_text(fx.fixed_width_table(4000), encoding="utf-8")
(root / "docx" / "synthetic-report-images.docx").write_bytes(fx.make_docx(1))
(root / "docx" / "synthetic-report-images-2.docx").write_bytes(fx.make_docx(2))
(root / "pdf" / "synthetic-reportlab-fonts-images.pdf").write_bytes(fx.make_pdf(1))
print("synthetic corpus written")
