# Installation Guide – Dentec Dental Lab Manager

Target computer: one Windows 10 or Windows 11 PC in the office (64-bit). No internet connection is
needed to use the program.

## Option A – Install the ready-made program (recommended)

1. Get **DentecLab-Windows.zip** (from the GitHub *Actions* build artifact, or from whoever built it).
2. Right-click the zip → **Extract All…** → extract to `C:\DentecLab` (any folder works; avoid
   the Downloads folder).
3. Open `C:\DentecLab\DentecLab` and double-click **DentecLab.exe**.
   * If Windows SmartScreen says "Windows protected your PC", click **More info → Run anyway**
     (the program is not code-signed; signing certificates cost money every year).
4. Right-click `DentecLab.exe` → **Send to → Desktop (create shortcut)** so staff can start it easily.
5. On first start the program asks you to **create the administrator account**. Choose a strong
   password and keep it somewhere safe – it is needed to manage users, restore backups, authorize
   price changes and issue credit notes.

The zip already contains Tesseract OCR (in the `tesseract` folder), so the Lab Slip Scanner works
immediately.

### Where the data is stored

By default everything is stored in **`Documents\DentecLab`** of the Windows user:

```
Documents\DentecLab\
  data\dentec.db        the database
  documents\            original lab slip images / PDFs
  invoices\             invoice PDFs (by billing month)
  reports\              exported reports, delivery lists
  backups\              automatic and manual backups
  logs\                 error log (no patient data)
```

*Portable mode:* create an empty file named `portable.txt` next to `DentecLab.exe`; data is then kept
in a `DentecData` folder beside the program (useful on a dedicated drive).

### Try it with sample data first

Start the program once with the fictional sample database (kept in a separate
`Documents\DentecLab_SAMPLE` folder, so your real data is never touched):

```
C:\DentecLab\DentecLab\DentecLab.exe --sample
```

Sample sign-ins: **admin / dentec-admin** (administrator) and **frontdesk / dentec-staff** (staff).
Sample lab slips to try in the Lab Slip Scanner are in `sample_data\labslips`.

### Check the installation

`DentecLab.exe --self-test --ui` runs an offline check (database, prices, invoice PDF, backup, OCR,
all screens) and writes the result to `dentec_selftest.txt` in the current folder. It must end with
`SELF-TEST PASSED`.

## Option B – Build the program yourself on the office PC

1. Install **Python 3.12** (or newer) from <https://www.python.org/downloads/windows/>.
   Tick **"Add python.exe to PATH"** during setup.
2. Optional (for OCR): install **Tesseract OCR** for Windows (free) from the UB Mannheim builds:
   <https://github.com/UB-Mannheim/tesseract/wiki> → `tesseract-ocr-w64-setup-….exe`, default folder
   `C:\Program Files\Tesseract-OCR`.
3. Copy the `dentec_lab` folder to the PC and double-click **`build_windows.bat`**.
   It creates a private Python environment, installs the components, runs the automated tests,
   builds `dist\DentecLab\DentecLab.exe`, copies the sample data/docs, bundles Tesseract (if
   installed) and runs the self-test.
4. Copy the `dist\DentecLab` folder to `C:\DentecLab` and continue with step 3 of Option A.

## Recommended Windows security settings (patient privacy)

* Give each employee their **own Windows account** and their own Dentec sign-in.
* Turn on **BitLocker / Device encryption** (Settings → Privacy & security → Device encryption) so
  the database and slip images are encrypted if the PC is stolen.
* Keep Windows Update and Microsoft Defender turned on.
* Set the Windows lock screen to activate after a few minutes; the program also locks itself after
  15 minutes of inactivity (configurable).
* Copy backups regularly to an external drive kept in a safe place (see the backup guide).

## Uninstall

Delete the program folder (`C:\DentecLab`). Your data in `Documents\DentecLab` is **not** removed –
delete it only if you are sure you no longer need it (keep a backup!).

## Costs

All components are free and open-source (Python, Qt/PySide6 LGPL, SQLite, SQLAlchemy, ReportLab,
OpenCV, Tesseract, PyMuPDF AGPL, PyInstaller). There are no licence or subscription fees. Optional
costs: none required; a code-signing certificate (to avoid the SmartScreen warning) would be extra.
