# User Manual – Dentec Dental Lab Manager

This manual follows a normal month at Dentec Dental Lab. Keep the paper lab slips exactly as today –
the program only replaces the typing and adding-up at month end.

---

## 1. Signing in

* Each employee has their own **username and password**. Administrators create accounts in
  **Backup & Settings → Users & Security**. New users choose their own password at first sign-in.
* The program **locks itself after 15 minutes** without use (changeable). Sign in again to continue;
  nothing is lost.
* Every important action (records saved, invoices finalized, payments, price changes, backups,
  sign-ins) is written to the **Activity Log** (Backup & Settings → Activity Log, administrators only).

Roles:

| | Staff | Administrator |
|---|---|---|
| Enter records, scan slips, create & finalize invoices, record payments, reports, backups | ✔ | ✔ |
| Change a price on a record (manual adjustment) | needs an administrator to type their password | ✔ |
| Credit notes, void invoices, delete payments | – | ✔ |
| Settings, tax, users, restore a backup | – | ✔ |

---

## 2. Dental Offices

Sidebar → **Dental Offices**.

* **+ New Office**, fill in the profile (only the office name is required) and click **Save Office**.
  You are then asked how to start the office's price list:
  * *Dentec reference prices* – copies the standard price list, ready to adjust;
  * *Copy the price list of another office* – for offices with the same negotiated prices;
  * *Empty* – enter prices later.
* **Billing preferences**: send invoices by Print/Email/Both, invoice e-mail, payment terms,
  *Tax exempt* (new lines default to non-taxable) and *Show previous balance on invoices*.
* **Account Status**: Active, On Hold or Inactive. Offices with history cannot be deleted – set them to
  *Inactive* instead; their history stays available.
* The tabs **Case History, Invoices, Payments, Balance** show everything for the selected office.
  Every office is completely separate; offices are never combined on one invoice.

---

## 3. Price List

Sidebar → **Price List**. At the top choose **Dentec reference prices** or a dental office.

* Double-click a **Price** cell, type the new price, then click **Save Price Changes**
  (optionally type a reason first – it is stored in the price history).
* Clearing a price removes the office's negotiated price; the reference price is then suggested
  (shown with the reference price in the procedure list).
* **Copy From Office…** – copy another office's prices into the selected office.
* **Bulk Increase/Decrease…** – change all (or ticked) prices by a percentage or dollar amount,
  with rounding (e.g. +3 %, rounded to the nearest $1).
* **Import CSV/Excel…** – columns `Procedure` and `Price` (optional `Category`, `Taxable`). Unknown
  procedures are created as custom procedures. **Export…** writes the same format.
* **Price History** – every change with old/new price, date, user and reason.
* **+ New Custom Procedure** – add procedures that are not in the list. Double-click a procedure name
  to rename it, change its category, default tax status, or make it **inactive** (hidden from new
  records, kept for history).

**Important:** changing a price never changes records already entered or invoices already finalized.
Each record keeps the exact price charged. *Teeth Try-In* and *Full Denture Finish* are separate items
even though both are $99.

---

## 4. Monthly Case Entry (month-end batch entry)

Sidebar → **Monthly Case Entry**.

1. Choose the **Dental Office** and the **Billing Month** at the top.
2. Sort that office's paper slips and enter them one after another:
   * **Patient Name** (required), optional **Case Number**, **Date**, **Due Date**.
   * One **line per procedure**: Arch (Upper / Lower / Both), Procedure, Additional Description,
     Tooth #, Qty, Unit Price, Tax. The office's own price is filled in automatically.
   * Anything not in the list: leave Procedure on *(free-text description)*, type the description
     (e.g. *2 Clasp*) and the price.
   * Choosing **Both** sets quantity to 2 (one per arch) – change it if you charge once.
   * Several procedures for one patient: **+ Add Line** (Ctrl+L).
   * Optional **internal notes** (never printed) and **Attach image/PDF…** for a scan of the slip.
3. Click **Save & Approve for Billing** (Ctrl+Enter) – the form clears for the next slip – or
   **Save Draft** (Ctrl+S) if something is still unclear.

Examples of how lines appear on the invoice:

| You enter | Invoice shows |
|---|---|
| Upper Bite Block + Lower Bite Block | *Patient A – Upper, Lower Bite Block (x2 @ $35.00)* |
| Upper Teeth Try-In + Lower Teeth Try-In | *Patient B – Upper, Lower Teeth Try-In (x2 @ $99.00)* |
| Upper Full Denture Finish + free text "2 Clasp" Lower $45 | *Patient C – Upper Full Denture Finish* / *Patient C – Lower 2 Clasp* |
| Lower Flexible Partial, description "Finish" | *Patient D – Lower Flexible Partial - Finish* |

* The list on the left shows all records of the batch with their status: **Draft**, **Approved**,
  **On draft invoice**, **Invoiced #…**. Select several and use **Approve Selected** / **Back to Draft**.
* The search box finds patients, case numbers or procedures in the batch.
* Only **Approved** records are invoiced. Drafts are skipped and stay for later.
* A price different from the office price is highlighted; staff need an administrator to authorize it.
* Records on a **finalized** invoice are locked. Corrections are made with a credit note (section 7).

---

## 5. Lab Slip Scanner (optional OCR)

Sidebar → **Lab Slip Scanner**. Scan the slips (any scanner/copier; 200–300 dpi, black & white or
grey) to JPG, PNG or PDF.

1. **Import Lab Slips…** – choose one or many files; multi-page PDFs are split into pages.
   The originals are copied into the local document store and kept.
2. Click a slip in the queue. The **original image** appears beside the **extracted data**:
   doctor, office, phone, address, patient, sex, age, date, due date, check marks (procedure, alloy,
   margins, denture, restoration type), tooth chart, shade, designs, signature, license number.
3. **Yellow fields are uncertain.** Compare them with the image (use Zoom) and correct them. The tooth
   chart always has to be checked by a person.
4. The office is matched automatically from the slip (office name, dentist or phone). **Confirm it.**
5. Billing lines are **suggested** from the check marks (e.g. *Bite Block* + *FUD* + *FLD* → Upper and
   Lower Bite Block; *Zirconia Full Contour* + teeth 3, 14 → 2 × Full Zirconia Crown). Review them.
   The technical slip details (alloy, margins, shade…) are stored with the record but are **not**
   billed.
6. Tick **"I have verified the flagged values"** and click **Approve & Save Record**. The record is
   created in Monthly Case Entry with the slip image attached. Nothing is saved before approval;
   **Reject** removes a slip from the queue (the image is kept).

If Tesseract is not installed the scanner still works as an "image beside the form" entry screen.

*Tuning:* field labels, check-box names and billing suggestions are defined in
`labslip_template.json` (shown under Backup & Settings → OCR). An administrator can adjust it to the
exact wording printed on the lab slip.

---

## 6. Monthly Invoices

Sidebar → **Monthly Invoices**.

1. Choose the **Billing month**. The *Billing run* lists every office with approved records that are
   not yet invoiced.
2. Select one or more offices → **Create Draft Invoice(s) for Selected**.
3. In the invoice window:
   * **Type the invoice number.** The program never creates or increments invoice numbers; each
     office can follow its own sequence (e.g. Valley 1049, Sunrise 205, Riverpark 318). The last
     number used for that office is shown only as a reminder. A number already used anywhere is
     highlighted and you are asked to confirm.
   * Check invoice date, tax rate, Bill To / Ship To and the lines. **Refresh From Records** pulls in
     late-approved records; **Remove Selected Record** leaves a record for a later invoice;
     **Add Manual Line…** adds fees or discounts.
   * **Preview PDF** – a draft preview (marked *DRAFT PREVIEW*).
   * **Finalize Invoice** – locks the invoice and its records, saves the PDF in
     `Documents\DentecLab\invoices\YYYY-MM\` and offers to print.
4. **Email** sends the PDF from your own e-mail account (set up in Settings → E-mail); otherwise the
   PDF folder opens so you can attach it yourself.

The invoice is black & white, US Letter: company header, invoice number/date/billing period,
Bill To / Ship To, itemized table (Description – patient, arch, procedure, notes | Tax | Amount),
Subtotal, Tax Rate, Tax, Invoice Total, Previous Balance, Total Amount Due and the account aging
(0–30, 31–60, 61–90, over 90 days, total outstanding). Long months continue on further pages
(*Page 1 of 2* …).

**Duplicate billing is impossible:** a record can belong to only one invoice. A second invoice for
the same office and month is possible (e.g. late slips) and only contains records not billed before.

**Finding / reprinting old invoices:** use the filters under *Invoices* (office, status, invoice
number, billing month) → **Reprint / Save PDF** or **Print**. Reprints show the invoice exactly as
issued (including the aging at that date).

**Corrections after finalizing** (administrator): open the invoice →
* **Credit Note…** – enter its number, amount, taxable part and description. It reduces the
  invoice's open balance and prints as a separate *CREDIT NOTE*.
* **Void…** – only for invoices without payments/credits; records are released and can be invoiced
  again with a new number.

---

## 7. Payments

Sidebar → **Payments** → choose the office.

* *Open invoices* shows every unpaid or partly paid invoice, oldest first.
* Enter **date, amount, method** (Cash, Check, Credit Card, Bank Transfer, Other), **check number**
  and notes. *Apply to*: a specific invoice, or *Oldest open invoices first*. **Pay Full Balance of
  Selected** fills in the open amount of the selected invoice.
* Partial and multiple payments per invoice are supported. Status changes automatically:
  **Unpaid → Partially Paid → Paid**.
* Money paid beyond what is owed stays as **unapplied credit** on the account and reduces the
  balance; use **Apply Unapplied Credit…** when the next invoice exists.
* Administrators can delete a wrongly entered payment (a reason is logged).

---

## 8. Balances and aging

Each invoice's open balance = total − payments − credit notes. Balances are grouped by invoice age:
**0–30, 31–60, 61–90, over 90 days**. On a new invoice the **Previous Balance** (all older open
invoices) is shown separately from the current month's charges, and the **Total Amount Due** is the
sum – each old invoice is counted once, never twice. Turn the previous-balance section off per office
in the office's billing preferences.

---

## 9. Tax

Backup & Settings → **Tax** (administrator). Nothing is hard-coded and the starting rate is **0 %**
until you confirm the correct treatment with your tax professional.

* **Tax rate** (e.g. 7.975) and **calculation method** (round once on the taxable subtotal, or per
  line).
* Tick which **procedures are taxable** by default. Every record line can still be switched
  individually; *tax-exempt* offices default to non-taxable.
* Tax is calculated only on taxable lines. Each invoice stores its own rate and amounts, so later
  changes never alter finalized invoices.

---

## 10. Reports

Sidebar → **Reports**: Monthly Revenue, Revenue by Dental Office, Revenue by Procedure, Outstanding
Balances (aging), Payment History, Monthly Case Count, Monthly Invoice Summary. Choose options →
**Run** → **Export CSV…** (opens in Excel) or **Export PDF…**. Revenue counts finalized invoices;
credit notes are subtracted.

---

## 11. Deliveries (optional)

Sidebar → **Deliveries**. Choose the next Tuesday/Thursday, select cases due (bottom list) and
**Create Delivery Run**. The run is grouped by office; tick cases as delivered or **Mark Run
Completed**; **Print Delivery List** for the driver. Previous runs can be searched. Deliveries never
affect invoicing. Delivery days can be changed in Settings → Company & Invoice.

---

## 12. Daily routine checklist

* During the month: keep paper slips as usual (optionally scan them).
* Month end: enter or scan all slips per office → approve → Monthly Invoices → type numbers →
  finalize → print/e-mail.
* When payments arrive: Payments.
* Weekly: copy the newest backup to an external drive (see BACKUP_RESTORE.md).

## 13. Help with problems

* Error details are written to `Documents\DentecLab\logs\dentec.log` (no patient data).
* "No price on file" – enter a unit price on the line, or add the price in Price List.
* "This record is on a finalized invoice" – use a credit note.
* OCR not available – install Tesseract or set its path in Settings → OCR.
