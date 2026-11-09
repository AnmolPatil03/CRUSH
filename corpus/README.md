# Benchmark corpus

| folder | files | source | licence |
|---|---|---|---|
| `txt/` | alice, pride-and-prejudice, moby-dick | Project Gutenberg via github.com/GITenberg | public domain (Gutenberg licence header kept) |
| `txt/` | synthetic-fixed-width-table | `scripts/make_corpus.py` | generated |
| `docx/` | `word-*.docx` - made by Microsoft Word (Windows and Mac) | python-docx test files, github.com/python-openxml/python-docx | MIT |
| `docx/` | `synthetic-report-images*.docx` | `scripts/make_corpus.py` (python-docx, zlib-deflated) | generated |
| `pdf/` | pdflatex, LibreOffice, pdfkit, ImageMagick, ReportLab, CMYK/grayscale samples | github.com/py-pdf/sample-files | CC BY-SA 4.0 |
| `pdf/` | alice-gutenberg.pdf | Project Gutenberg | public domain |
| `pdf/` | synthetic-reportlab-fonts-images.pdf | `scripts/make_corpus.py` | generated |

Add your own documents to any folder and re-run `crush bench corpus`.
