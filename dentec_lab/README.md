# Dentec Dental Lab Manager

Windows desktop application for **Dentec Dental Lab** (2962 E Plymouth Way, Fresno, CA 93720) that
automates month-end invoicing while keeping the paper lab-slip workflow.

* One-time cost: **$0** – built only on free, open-source components. No subscription, no cloud.
* Works **completely offline**. All data (database, lab-slip images, invoices, backups) stays on the office PC.
* Local SQLite database, local OCR (Tesseract + OpenCV). Patient data is never sent to any outside service.

## What it does

| Screen | Purpose |
|---|---|
| Dashboard | Outstanding balances, records waiting to be invoiced, slips to review, last backup |
| Dental Offices | Separate customer profile per office: contacts, addresses, billing preferences, case/invoice/payment history, balance |
| Monthly Case Entry | Fast **month-end batch entry**: one record per lab slip, several procedures per patient, free-text lines, drafts, attachments |
| Lab Slip Scanner | Import JPG/PNG/PDF slips (many at once), OCR beside the original image, correct, approve |
| Price List | Dentec reference prices + each office's negotiated prices, copy, bulk change, CSV/Excel import/export, history |
| Monthly Invoices | One consolidated invoice per office per month, **manually typed invoice numbers**, preview, finalize, reprint, credit notes |
| Payments | Full/partial/multiple payments, check numbers, auto-apply to oldest, account credit |
| Reports | Revenue by month / office / procedure, A/R aging, payment history, case counts, invoice summary (CSV + PDF) |
| Deliveries | Optional Tuesday/Thursday delivery runs grouped by office |
| Backup & Settings | Daily automatic backup, manual backup/restore, export, users, tax, OCR, e-mail, activity log |

## Documentation

* [Installation guide](docs/INSTALLATION.md)
* [User manual](docs/USER_MANUAL.md)
* [Backup and restore](docs/BACKUP_RESTORE.md)

## Getting the Windows program (.exe)

The `.exe` is built automatically on a Windows machine by GitHub Actions
(`.github/workflows/build-windows.yml`): it runs the full test suite on Windows, builds
`DentecLab.exe` with PyInstaller, bundles Tesseract OCR, runs an offline self-test of the built
program (including opening every screen) and publishes **DentecLab-Windows.zip** as a build artifact
(repository → *Actions* → latest *Build Dentec Lab Manager* run → *Artifacts*).

To build it yourself on the office PC, run `build_windows.bat` (see the installation guide).

## For developers

```
cd dentec_lab
python -m pip install -r requirements-dev.txt
python main.py                 # run the app (first start asks you to create the admin account)
python main.py --sample        # run with the fictional sample database (DentecLab_SAMPLE folder)
python -m pytest -q            # 26 automated tests (business rules, OCR, PDF, backup, full UI workflow)
python main.py --self-test --ui
python scripts/make_sample_db.py   # rebuild sample_data/
```

Project layout:

```
dentec_lab/
  main.py                 entry point (+ --self-test, --sample)
  dentec/config.py        data folder locations
  dentec/money.py         exact money math (integer cents)
  dentec/db/              SQLAlchemy models, database setup, preloaded catalog & prices
  dentec/services/        business rules (offices, pricing, cases, invoices, payments, aging,
                          reports, deliveries, backup, auth, OCR import, e-mail)
  dentec/ocr/             OpenCV + Tesseract lab-slip reader, field template, sample form generator
  dentec/pdf/             black & white invoice PDF (ReportLab)
  dentec/ui/              PySide6 screens
  tests/                  pytest suite
  sample_data/            fictional sample database + sample lab slips
  DentecLab.spec          PyInstaller build file
  build_windows.bat       one-click Windows build
```

Technology: Python 3.12+, PySide6, SQLite + SQLAlchemy 2, ReportLab, OpenCV, Tesseract (pytesseract),
PyMuPDF (PDF page rendering), openpyxl (Excel import/export), PyInstaller.

## Notes on the supplied reference files

The original `DT lapslip.pdf` and the existing invoice image were **not included in the repository**.
The OCR was therefore built from the field list in the specification and tested against generated
lab slips with the same fields (`dentec/ocr/sample_form.py`, `sample_data/labslips/`). Recognition of
the real form is driven by `dentec/ocr/labslip_template.json` (label names, check-box names, billing
suggestions), which can be tuned once real scanned slips are available — see the user manual. The
invoice layout follows the written specification (black & white, US Letter, Description/Tax/Amount,
aging table).
