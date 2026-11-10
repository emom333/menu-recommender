# Backup and Restore – Dentec Dental Lab Manager

All business data lives on the office PC. A backup protects you against a broken disk, theft,
ransomware or accidental changes.

## What a backup contains

One file `DentecBackup_YYYYMMDD_HHMMSS_<type>.zip` with:

* `dentec.db` – a consistent snapshot of the database (offices, prices, records, invoices, payments,
  users, activity log), taken safely even while the program is running;
* `documents/` – every stored lab-slip image/PDF;
* `dentec_backup.json` – date and program version.

Invoice PDFs are not needed in the backup – they can be re-created at any time from the database
(Monthly Invoices → Reprint).

> Backups contain patient information. Store them only on drives you control (encrypted USB drive
> or BitLocker-protected external disk). Do not upload them to public cloud storage.

## Automatic daily backup

On by default (Backup & Settings → Backup → *Automatic daily backup*). While the program is open, a
backup is made when the previous one is older than 24 hours, and again when the program is closed if
one is due. The last 30 automatic backups are kept (changeable); manual backups are never deleted
automatically. The Dashboard shows the time of the last backup.

Default folder: `Documents\DentecLab\backups`. You can choose another folder, e.g. a second internal
disk or a network drive (**Browse…**, then **Save Backup Settings**).

## Manual backup

Backup & Settings → **Back Up Now**. Do this before large changes (bulk price changes, restores,
updates of the program).

## Copy backups off the computer (weekly)

1. Plug in the external (encrypted) drive.
2. Backup & Settings → **Open Backup Folder**.
3. Copy the newest `DentecBackup_….zip` to the external drive.
4. Keep at least the last 4 weekly copies; store the drive away from the PC.

## Restore from a backup (administrator)

1. Backup & Settings → Backup → **Restore From Backup…** and choose the `.zip` file.
2. The file is checked first (zip intact, database integrity check, correct program). Damaged or
   foreign files are refused.
3. Confirm. The program first makes a **safety backup** of the current data
   (`…_before_restore.zip`), then replaces the database and the lab-slip documents with the backup.
4. Sign in again. Everything entered after the backup was made is no longer present – re-enter it,
   or restore the safety backup if you chose the wrong file.

### Restoring on a new computer

1. Install the program (see INSTALLATION.md) and start it once (create a temporary admin).
2. Copy the backup file to the new PC.
3. Backup & Settings → Restore From Backup… → choose the file. Sign in with the users from the backup.

### Emergency restore without the program

The backup is an ordinary zip file. With the program closed, extract `dentec.db` to
`Documents\DentecLab\data\dentec.db` (rename the old file first) and the `documents` folder to
`Documents\DentecLab\documents`.

## Database export

Backup & Settings → **Export Database (CSV)…** creates a zip with every table as a CSV file (opens in
Excel) plus a copy of the SQLite database – useful for an accountant, for archiving, or for moving to
another system. Password hashes are never exported.
