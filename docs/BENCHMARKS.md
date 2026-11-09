# CRUSH benchmark

Corpus: `corpus`


## docx - 12 files, 487,949 bytes (CRUSH peak memory 498 MiB)

| codec | size | saved | bits/byte | enc MB/s | dec MB/s |
|---|---:|---:|---:|---:|---:|
| crush | 283,866 | 41.8% | 4.654 | 0.04 | 0.11 |
| zstd-19 | 386,375 | 20.8% | 6.335 | 12.12 | 910.85 |
| lzma-9e | 387,616 | 20.6% | 6.355 | 2.43 | 46.68 |
| zlib-9 | 391,328 | 19.8% | 6.416 | 52.28 | 306.23 |
| bz2-9 | 404,955 | 17.0% | 6.639 | 9.21 | 27.46 |

| file | size | CRUSH | best baseline |
|---|---:|---:|---:|
| synthetic-report-images-2.docx | 58,590 | 41.3% | 5.5% (zstd-19) |
| synthetic-report-images.docx | 58,516 | 41.2% | 5.5% (zstd-19) |
| word-blk-paras-and-tables.docx | 15,649 | 44.5% | 20.1% (zstd-19) |
| word-comments-rich-para.docx | 20,023 | 35.1% | 17.6% (zstd-19) |
| word-doc-odd-even-hdrs.docx | 17,711 | 50.9% | 20.6% (zstd-19) |
| word-hdr-header-footer.docx | 18,079 | 50.5% | 21.0% (zstd-19) |
| word-num-having-numbering-part.docx | 24,334 | 53.2% | 40.5% (zstd-19) |
| word-sct-section-props.docx | 28,168 | 57.9% | 46.6% (zstd-19) |
| word-shp-inline-shape-access.docx | 122,610 | 31.8% | 16.4% (zstd-19) |
| word-tbl-cell-access.docx | 36,051 | 46.0% | 32.9% (lzma-9e) |
| word-tbl-having-applied-style.docx | 50,294 | 44.9% | 33.3% (lzma-9e) |
| word-txt-font-props.docx | 37,924 | 42.7% | 25.6% (lzma-9e) |

## pdf - 12 files, 3,281,763 bytes (CRUSH peak memory 445 MiB)

| codec | size | saved | bits/byte | enc MB/s | dec MB/s |
|---|---:|---:|---:|---:|---:|
| crush | 1,726,004 | 47.4% | 4.208 | 0.14 | 0.26 |
| lzma-9e | 2,373,732 | 27.7% | 5.786 | 2.96 | 63.26 |
| zstd-19 | 2,379,183 | 27.5% | 5.800 | 8.03 | 1992.84 |
| bz2-9 | 2,427,342 | 26.0% | 5.917 | 14.31 | 25.03 |
| zlib-9 | 2,444,292 | 25.5% | 5.958 | 54.00 | 423.64 |

| file | size | CRUSH | best baseline |
|---|---:|---:|---:|
| alice-gutenberg.pdf | 711,671 | 55.8% | 29.1% (zstd-19) |
| cmyk-image.pdf | 443,953 | 16.9% | 0.2% (zstd-19) |
| grayscale-image.pdf | 40,115 | 14.8% | 0.8% (zstd-19) |
| imagemagick-images.pdf | 16,012 | 76.4% | 82.7% (lzma-9e) |
| latex-multicolumn.pdf | 78,657 | 8.7% | 1.1% (zstd-19) |
| libreoffice-writer.pdf | 12,609 | 12.1% | 6.2% (zstd-19) |
| pdfkit.pdf | 14,404 | 46.3% | 17.8% (lzma-9e) |
| pdflatex-4-pages.pdf | 24,607 | 11.6% | 2.6% (zstd-19) |
| pdflatex-geotopo-compressed.pdf | 1,793,479 | 57.2% | 37.2% (lzma-9e) |
| pdflatex-image.pdf | 74,061 | 8.6% | 4.2% (zstd-19) |
| reportlab-inline-image.pdf | 1,537 | 37.2% | 43.7% (zlib-9) |
| synthetic-reportlab-fonts-images.pdf | 70,658 | 21.6% | 18.0% (lzma-9e) |

## txt - 4 files, 2,370,570 bytes (CRUSH peak memory 438 MiB)

| codec | size | saved | bits/byte | enc MB/s | dec MB/s |
|---|---:|---:|---:|---:|---:|
| crush | 542,795 | 77.1% | 1.832 | 0.57 | 0.59 |
| bz2-9 | 654,319 | 72.4% | 2.208 | 15.62 | 27.18 |
| lzma-9e | 722,688 | 69.5% | 2.439 | 2.51 | 109.88 |
| zstd-19 | 735,513 | 69.0% | 2.482 | 3.21 | 1193.12 |
| zlib-9 | 882,441 | 62.8% | 2.978 | 22.35 | 534.86 |

| file | size | CRUSH | best baseline |
|---|---:|---:|---:|
| alice.txt | 173,595 | 75.8% | 71.7% (bz2-9) |
| moby-dick.txt | 1,276,201 | 74.7% | 69.5% (bz2-9) |
| pride-and-prejudice.txt | 724,725 | 78.4% | 74.8% (bz2-9) |
| synthetic-fixed-width-table.txt | 196,049 | 89.2% | 82.8% (bz2-9) |

## ALL - 28 files, 6,140,282 bytes

| codec | size | saved | bits/byte | enc MB/s | dec MB/s |
|---|---:|---:|---:|---:|---:|
| crush | 2,552,665 | 58.4% | 3.326 | 0.15 | 0.29 |
| lzma-9e | 3,484,036 | 43.3% | 4.539 | 2.72 | 73.18 |
| bz2-9 | 3,486,616 | 43.2% | 4.543 | 14.15 | 26.01 |
| zstd-19 | 3,501,071 | 43.0% | 4.561 | 5.17 | 1472.72 |
| zlib-9 | 3,718,061 | 39.4% | 4.844 | 34.86 | 445.85 |

## Ablation (text corpus)

| configuration | bytes | bits/char |
|---|---:|---:|
| orders 0-6 only (fixed weights) | 678,271 | 2.289 |
| + learned mixer | 585,164 | 1.975 |
| + APM/SSE | 577,735 | 1.950 |
| + word + column/record models | 546,763 | 1.845 |
| + match model | 542,723 | 1.832 |
| + solid mode (all files in one archive) | 527,580 | 1.780 |

## Image codecs (lossless pixels, bytes)

| file | size | raw | CM | pyramid | JPEG XL |
|---|---|---:|---:|---:|---:|
| synthetic-report-images-2.docx | 300x200x3 | 180,000 | 298 | 4,196 | 102 |
| synthetic-report-images.docx | 300x200x3 | 180,000 | 298 | 4,196 | 102 |
| word-shp-inline-shape-access.docx | 140x56x4 | 31,360 | 3,781 | 12,995 | 5,016 |
