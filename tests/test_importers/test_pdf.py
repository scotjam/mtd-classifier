"""Tests for the heuristic PDF statement importer."""

from datetime import date

import pytest

from mtd_classifier.importers.pdf_importer import PdfImporter
from mtd_classifier.importers.registry import detect_importer


def _make_pdf(path, lines):
    """Write a minimal valid single-page text PDF (Helvetica, one Tj per line)."""
    def esc(s):
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    content = "BT /F1 10 Tf 40 750 Td " + " ".join(
        f"({esc(line)}) Tj 0 -14 Td" for line in lines
    ) + " ET"
    content_bytes = content.encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content_bytes)).encode()
        + b" >>\nstream\n" + content_bytes + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(out))
    return path


@pytest.fixture
def statement_pdf(tmp_path):
    return _make_pdf(tmp_path / "statement.pdf", [
        "My Bank plc - Statement April 2026",
        "Date Description Amount Balance",
        "15/04/2026 ACME CORP SALARY 2,500.00 5,000.00",
        "16/04/2026 TESCO STORES 45.67 4,954.33",
        "17 Apr 2026 PLUMBER REFUND 20.00 4,974.33",
        "Interest rate 1.50% may apply",
    ])


def test_can_handle_and_registry(statement_pdf):
    importer = detect_importer(statement_pdf)
    assert isinstance(importer, PdfImporter)
    assert importer.generic is True


def test_parse_infers_signs_from_balance_movement(statement_pdf):
    txns = list(PdfImporter().parse(statement_pdf))
    assert len(txns) == 3
    assert txns[0].date == date(2026, 4, 15)
    assert txns[0].description == "ACME CORP SALARY"
    assert txns[0].amount == 2500.00
    # Debit printed unsigned, but the balance drop proves it is money out
    assert txns[1].amount == -45.67
    assert txns[1].balance == 4954.33
    # Refund: balance rises, amount stays positive
    assert txns[2].amount == 20.00
    # Original statement line preserved for the details panel
    assert "TESCO STORES" in txns[1].raw_data


def test_dr_marker_makes_single_amount_negative(tmp_path):
    pdf = _make_pdf(tmp_path / "dr.pdf", [
        "01/05/2026 CARD PAYMENT COFFEE 3.20 DR",
    ])
    txns = list(PdfImporter().parse(pdf))
    assert len(txns) == 1
    assert txns[0].amount == -3.20


def test_balance_brought_and_carried_forward_excluded(tmp_path):
    pdf = _make_pdf(tmp_path / "fd_statement.pdf", [
        "First Direct - Statement April 2026",
        "Date Description Amount Balance",
        "01 Apr 2026 BALANCE BROUGHT FORWARD 5,000.00",
        "15/04/2026 ACME CORP SALARY 2,500.00 7,500.00",
        "16/04/2026 TESCO STORES 45.67 7,454.33",
        "30 Apr 2026 BALANCE CARRIED FORWARD 7,454.33",
    ])
    txns = list(PdfImporter().parse(pdf))
    assert len(txns) == 2
    assert txns[0].description == "ACME CORP SALARY"
    assert txns[0].amount == 2500.00
    assert txns[1].description == "TESCO STORES"
    assert txns[1].amount == -45.67


def test_opening_and_closing_balance_lines_excluded(tmp_path):
    pdf = _make_pdf(tmp_path / "statement2.pdf", [
        "01/05/2026 OPENING BALANCE 1,000.00",
        "05/05/2026 CARD PAYMENT COFFEE 3.20 996.80",
        "31/05/2026 CLOSING BALANCE 996.80",
    ])
    txns = list(PdfImporter().parse(pdf))
    assert len(txns) == 1
    assert txns[0].description == "CARD PAYMENT COFFEE"
    assert txns[0].amount == -3.20


def test_balance_bf_cf_abbreviations_excluded(tmp_path):
    pdf = _make_pdf(tmp_path / "statement3.pdf", [
        "01/06/2026 BALANCE B/F 200.00",
        "02/06/2026 SHOP PURCHASE 10.00 190.00",
        "30/06/2026 BALANCE C/F 190.00",
    ])
    txns = list(PdfImporter().parse(pdf))
    assert len(txns) == 1
    assert txns[0].description == "SHOP PURCHASE"


def test_non_statement_pdf_rejected(tmp_path):
    pdf = _make_pdf(tmp_path / "letter.pdf", [
        "Dear Customer,",
        "Thank you for your letter of 2 June.",
    ])
    assert not PdfImporter().can_handle(pdf)
    assert detect_importer(pdf) is None


def _make_pdf_positioned(path, words):
    """Minimal PDF placing each (x, y, text) word individually - for
    testing column-position-aware parsing (IN/OUT/BALANCE layouts)."""
    return _make_pdf_positioned_pages(path, [words])


def _make_pdf_positioned_pages(path, pages):
    """Like _make_pdf_positioned, with one list of (x, y, text) words per page."""
    def esc(s):
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    # Objects: 1 catalog, 2 page tree, 3 font, then a page + content per page.
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, words in enumerate(pages):
        content_bytes = " ".join(
            f"BT /F1 10 Tf 1 0 0 1 {x} {y} Tm ({esc(text)}) Tj ET"
            for x, y, text in words
        ).encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {5 + 2 * i} 0 R /Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
        objects.append(
            b"<< /Length " + str(len(content_bytes)).encode()
            + b" >>\nstream\n" + content_bytes + b"\nendstream"
        )
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(out))
    return path


def _starling_like_pdf(path):
    """Reproduces Starling's layout: IN and OUT money columns whose
    distinction only survives in word x-positions, date-less OPENING
    BALANCE row, per-day END OF DAY balance, thousands without commas."""
    y = 700
    rows = [
        # header: IN right edge ~445, OUT ~501, BALANCE ~566
        (30, y, "DATE"), (93, y, "TYPE"), (191, y, "TRANSACTION"),
        (436, y, "IN"), (484, y, "OUT"), (528, y, "ACCOUNT"),
        (530, y - 12, "BALANCE"),
        (131, y - 24, "OPENING"), (168, y - 24, "BALANCE"), (531, y - 24, "£4454.08"),
        # money out, no balance printed
        (30, y - 36, "01/04/2025"), (93, y - 36, "DIRECT"), (140, y - 36, "DEBIT"),
        (200, y - 36, "Insurer"), (476, y - 36, "£15.99"),
        # money out with end-of-day balance
        (30, y - 48, "01/04/2025"), (93, y - 48, "FASTER"), (140, y - 48, "PAYMENT"),
        (200, y - 48, "Broker"), (471, y - 48, "£495.00"), (531, y - 48, "£3943.09"),
        # money IN - same text shape, different column
        (30, y - 60, "22/04/2025"), (93, y - 60, "FASTER"), (140, y - 60, "PAYMENT"),
        (200, y - 60, "Tenant"), (415, y - 60, "£638.00"), (531, y - 60, "£4581.09"),
        # large amount with no thousands separator, money IN
        (30, y - 72, "23/04/2025"), (93, y - 72, "FASTER"), (140, y - 72, "PAYMENT"),
        (200, y - 72, "Owner"), (400, y - 72, "£40000.00"), (528, y - 72, "£44581.09"),
        # footer noise with a date range and a money-like figure far from columns
        (30, y - 100, "Date"), (55, y - 100, "range:"),
        (90, y - 100, "01/04/2025"), (145, y - 100, "-"), (155, y - 100, "30/04/2025"),
        (230, y - 112, "£0.00"),
    ]
    return _make_pdf_positioned(path, rows)


def test_in_out_column_signs(tmp_path):
    pdf = _starling_like_pdf(tmp_path / "starling.pdf")
    txns = list(PdfImporter().parse(pdf))
    assert len(txns) == 4
    assert [t.amount for t in txns] == [-15.99, -495.00, 638.00, 40000.00]
    assert not any(t.ambiguous_sign for t in txns)
    assert txns[0].balance is None
    assert txns[1].balance == 3943.09
    assert txns[3].balance == 44581.09
    assert txns[2].description == "FASTER PAYMENT Tenant"


def test_numeric_statement_period(tmp_path):
    from datetime import date as d
    pdf = _starling_like_pdf(tmp_path / "starling.pdf")
    assert PdfImporter().statement_period(pdf) == (d(2025, 4, 1), d(2025, 4, 30))


def test_lloyds_style_money_in_out_columns(tmp_path):
    """Lloyds/Halifax layout: 'Money In (£) | Money Out (£) | Balance (£)'
    with In before Out, three-word dates ('02 Oct 23'), a dated STATEMENT
    OPENING BALANCE row and a closing row with totals in BOTH columns
    (which must not become a transaction)."""
    y = 700
    rows = [
        (30, y, "Date"), (100, y, "Description"), (260, y, "Type"),
        (330, y, "Money"), (365, y, "In"), (378, y, "(£)"),
        (440, y, "Money"), (475, y, "Out"), (495, y, "(£)"),
        (540, y, "Balance"), (578, y, "(£)"),
        # dated opening-balance row (Halifax prints these)
        (30, y - 14, "01"), (45, y - 14, "Oct"), (75, y - 14, "23"),
        (100, y - 14, "STATEMENT"), (160, y - 14, "OPENING"), (205, y - 14, "BALANCE"),
        (545, y - 14, "1881.76"),
        # money out
        (30, y - 28, "02"), (45, y - 28, "Oct"), (75, y - 28, "23"),
        (100, y - 28, "E17"), (125, y - 28, "SUPERMARKET"), (260, y - 28, "DEB"),
        (455, y - 28, "9.09"), (545, y - 28, "1872.67"),
        # money in
        (30, y - 42, "05"), (45, y - 42, "Oct"), (75, y - 42, "23"),
        (100, y - 42, "SANDHILL"), (260, y - 42, "FPI"),
        (350, y - 42, "625.00"), (545, y - 42, "2497.67"),
        # undated same-date row (shared printed date)
        (100, y - 56, "CO-OP"), (135, y - 56, "GROUP"), (175, y - 56, "FOOD"),
        (260, y - 56, "DEB"), (455, y - 56, "6.50"), (545, y - 56, "2491.17"),
        # closing summary with totals in BOTH columns - must be skipped
        (30, y - 70, "31"), (45, y - 70, "Oct"), (75, y - 70, "23"),
        (100, y - 70, "STATEMENT"), (160, y - 70, "CLOSING"), (205, y - 70, "BALANCE"),
        (350, y - 70, "625.00"), (450, y - 70, "15.59"), (545, y - 70, "2491.17"),
    ]
    pdf = _make_pdf_positioned(tmp_path / "lloyds.pdf", rows)
    txns = list(PdfImporter().parse(pdf))
    assert [t.amount for t in txns] == [-9.09, 625.00, -6.50]
    assert txns[0].date == date(2023, 10, 2)
    assert txns[2].date == date(2023, 10, 5)  # carried from the row above
    assert not any("BALANCE" in t.description for t in txns)


def test_natwest_style_withdrawn_paid_in_columns(tmp_path):
    """NatWest layout: 'Paid In(£) | Withdrawn(£) | Balance(£)' labels,
    yearless dates ('05 DEC') resolved via the printed period spanning a
    year boundary, and a BROUGHT FORWARD running-total row."""
    y = 700
    rows = [
        (30, y - 14, "Period"), (65, y - 14, "Covered"),
        (110, y - 14, "02"), (130, y - 14, "DEC"), (158, y - 14, "2023"),
        (188, y - 14, "to"), (205, y - 14, "03"), (225, y - 14, "JAN"), (253, y - 14, "2024"),
        (30, y - 28, "Date"), (100, y - 28, "Description"),
        (330, y - 28, "Paid"), (355, y - 28, "In(£)"),
        (440, y - 28, "Withdrawn(£)"), (540, y - 28, "Balance(£)"),
        (30, y - 42, "02"), (45, y - 42, "DEC"), (75, y - 42, "2023"),
        (100, y - 42, "BROUGHT"), (150, y - 42, "FORWARD"), (545, y - 42, "349284.09"),
        (30, y - 56, "05"), (45, y - 56, "DEC"),
        (100, y - 56, "OnLine"), (140, y - 56, "Transaction"), (200, y - 56, "HETTY"),
        (460, y - 56, "30.00"), (540, y - 56, "349254.09"),
        (30, y - 70, "02"), (45, y - 70, "JAN"),
        (100, y - 70, "Automated"), (155, y - 70, "Credit"), (190, y - 70, "WAGES"),
        (345, y - 70, "3289.58"), (540, y - 70, "352543.67"),
    ]
    pdf = _make_pdf_positioned(tmp_path / "natwest.pdf", rows)
    imp = PdfImporter()
    assert imp.statement_period(pdf) == (date(2023, 12, 2), date(2024, 1, 3))
    txns = list(imp.parse(pdf))
    assert len(txns) == 2
    assert txns[0].date == date(2023, 12, 5)   # year resolved to 2023
    assert txns[0].amount == -30.00            # Withdrawn column
    assert txns[1].date == date(2024, 1, 2)    # year resolved to 2024
    assert txns[1].amount == 3289.58           # Paid In column


def test_monzo_style_signed_amounts_without_commas(tmp_path):
    """Monzo layout: signed single amount + balance in plain text rows,
    large amounts printed without thousands separators."""
    pdf = _make_pdf(tmp_path / "monzo.pdf", [
        "Personal Account statement 01/11/2023 - 30/11/2023",
        "Date Description Amount Balance",
        "30/11/2023 Barstow Eves Estate Agents -1600.00 109.94",
        "29/11/2023 JOHN DAVID GROUP (Direct Credit) 1500.04 1709.94",
        "28/11/2023 Just Eat London GBR -13.73 4.90",
    ])
    imp = PdfImporter()
    assert imp.statement_period(pdf) == (date(2023, 11, 1), date(2023, 11, 30))
    txns = list(imp.parse(pdf))
    assert [t.amount for t in txns] == [-1600.00, 1500.04, -13.73]
    assert not any(t.ambiguous_sign for t in txns)


def test_metro_style_money_out_in_columns(tmp_path):
    """Metro Bank layout: 'MONEY OUT | MONEY IN | BALANCE' columns,
    three-word uppercase dates ('11 OCT 2023'), descriptions containing an
    embedded purchase date, wrapped continuation lines, sparse balances and
    a date-less Closing Balance summary row."""
    y = 700
    rows = [
        (30, y, "DATE"), (140, y, "TRANSACTION"),
        (390, y, "MONEY"), (425, y, "OUT"),
        (470, y, "MONEY"), (505, y, "IN"),
        (560, y, "BALANCE"),
        # money out, no balance, description embeds a second date
        (30, y - 14, "11"), (48, y - 14, "OCT"), (75, y - 14, "2023"),
        (140, y - 14, "Card"), (168, y - 14, "Purchase"), (215, y - 14, "09"),
        (232, y - 14, "OCT"), (258, y - 14, "2023"), (290, y - 14, "BOOKING.COM"),
        (405, y - 14, "390.00"),
        # wrapped continuation line - no date, no money
        (140, y - 26, "LONDON"), (185, y - 26, "GBR"), (212, y - 26, "GBR"),
        # money out with balance
        (30, y - 40, "14"), (48, y - 40, "OCT"), (75, y - 40, "2023"),
        (140, y - 40, "Outwards"), (190, y - 40, "Payment"), (238, y - 40, "Sergei"),
        (275, y - 40, "Bertov"), (405, y - 40, "900.00"), (555, y - 40, "3106.10"),
        # money in
        (30, y - 54, "30"), (48, y - 54, "OCT"), (75, y - 54, "2023"),
        (140, y - 54, "Cash"), (170, y - 54, "Deposit"),
        (485, y - 54, "620.00"), (555, y - 54, "3202.30"),
        # date-less closing summary - must be skipped
        (140, y - 68, "Closing"), (182, y - 68, "Balance"), (555, y - 68, "2790.30"),
    ]
    pdf = _make_pdf_positioned(tmp_path / "metro.pdf", rows)
    txns = list(PdfImporter().parse(pdf))
    assert [t.amount for t in txns] == [-390.00, -900.00, 620.00]
    assert txns[0].date == date(2023, 10, 11)
    assert "BOOKING.COM" in txns[0].description
    assert txns[1].balance == 3106.10
    assert not any("Closing" in t.description for t in txns)


def test_tsb_style_lowercase_month_period():
    """TSB prints 'Effective from: 02 december 2023 to 21 december 2023'
    with lowercase month names."""
    from mtd_classifier.importers.pdf_importer import _find_period_in_texts
    period = _find_period_in_texts(
        ["Effective from: 02 december 2023 to 21 december 2023"]
    )
    assert period == (date(2023, 12, 2), date(2023, 12, 21))


def test_revolut_style_columns_and_subrows(tmp_path):
    """Revolut layout: 'Money out | Money in | Balance', single-digit
    three-word dates ('3 Sep 2021'), and dateless sub-rows echoing the
    amount (card metadata) that must not become transactions."""
    y = 700
    rows = [
        (30, y, "Date"), (120, y, "Description"),
        (380, y, "Money"), (415, y, "out"),
        (470, y, "Money"), (505, y, "in"),
        (560, y, "Balance"),
        (30, y - 14, "3"), (40, y - 14, "Sep"), (64, y - 14, "2021"),
        (120, y - 14, "Payment"), (165, y - 14, "from"), (192, y - 14, "Terra"),
        (490, y - 14, "£75.00"), (560, y - 14, "£75.00"),
        (30, y - 28, "5"), (40, y - 28, "Sep"), (64, y - 28, "2021"),
        (120, y - 28, "To"), (140, y - 28, "Simon"), (178, y - 28, "Whitfield"),
        (405, y - 28, "£5.00"), (563, y - 28, "£70.00"),
        # dateless sub-row echoing the amount - description empty, skip
        (405, y - 40, "£5.00"),
        # dateless metadata sub-row - no money, skip
        (120, y - 52, "To:"), (145, y - 52, "22782735"),
        # refund lands in the Money in column
        (30, y - 66, "11"), (48, y - 66, "Sep"), (72, y - 66, "2021"),
        (120, y - 66, "Refund"), (160, y - 66, "from"), (188, y - 66, "Deliveroo"),
        (485, y - 66, "£17.55"), (560, y - 66, "£87.55"),
    ]
    pdf = _make_pdf_positioned(tmp_path / "revolut.pdf", rows)
    txns = list(PdfImporter().parse(pdf))
    assert [t.amount for t in txns] == [75.00, -5.00, 17.55]
    assert txns[0].date == date(2021, 9, 3)
    assert txns[2].description == "Refund from Deliveroo"


def test_tsb_style_table_with_overdraft_markers(tmp_path):
    """TSB layout: Lloyds-family 'Money Out (£) | Money In (£) | Balance (£)'
    table with two-digit-year dates ('02 Mar 26'), a dated STATEMENT
    OPENING BALANCE row, and overdrawn balances marked with an OD suffix
    (which must negate the balance, not pollute the description)."""
    y = 700
    rows = [
        (30, y, "Date"), (90, y, "Payment"), (133, y, "type"), (170, y, "Details"),
        (380, y, "Money"), (415, y, "Out"), (437, y, "(£)"),
        (470, y, "Money"), (505, y, "In"), (520, y, "(£)"),
        (560, y, "Balance"),
        (30, y - 14, "01"), (48, y - 14, "Mar"), (72, y - 14, "26"),
        (170, y - 14, "STATEMENT"), (230, y - 14, "OPENING"), (275, y - 14, "BALANCE"),
        (565, y - 14, "6.90"), (590, y - 14, "OD"),
        (30, y - 28, "04"), (48, y - 28, "Mar"), (72, y - 28, "26"),
        (90, y - 28, "DIRECT"), (128, y - 28, "DEBIT"), (170, y - 28, "VIRGIN"), (210, y - 28, "ACTIVE"),
        (400, y - 28, "119.99"), (555, y - 28, "126.95"), (595, y - 28, "OD"),
        (30, y - 42, "07"), (48, y - 42, "Mar"), (72, y - 42, "26"),
        (90, y - 42, "TRANSFER"), (170, y - 42, "FROM"), (200, y - 42, "Easy"), (228, y - 42, "Saver"),
        (490, y - 42, "20.00"), (560, y - 42, "13.05"),
    ]
    pdf = _make_pdf_positioned(tmp_path / "tsb.pdf", rows)
    txns = list(PdfImporter().parse(pdf))
    assert len(txns) == 2
    assert txns[0].amount == -119.99
    assert txns[0].balance == -126.95  # OD suffix negates the balance
    assert "OD" not in txns[0].description
    assert txns[1].amount == 20.00
    assert txns[1].balance == 13.05
    assert not any("OPENING" in t.description for t in txns)


def test_nationwide_style_yearless_dates_no_period(tmp_path):
    """Nationwide layout: '£ Out | £ In | £ Balance' columns, yearless
    dates ('02 Mar') with NO printed period phrase - the year comes from
    the latest full date in the document (the statement date). Includes a
    'Balance from statement...' opening row led by a bare year token and
    dateless same-day continuation rows."""
    y = 700
    rows = [
        (30, y, "Date"), (100, y, "Description"),
        (400, y, "£"), (410, y, "Out"), (470, y, "£"), (480, y, "In"),
        (540, y, "£"), (550, y, "Balance"),
        # opening row: bare year token + balance only
        (30, y - 14, "2020"), (100, y - 14, "Balance"), (145, y - 14, "from"),
        (172, y - 14, "statement"), (225, y - 14, "12"),
        (545, y - 14, "20,461.88"),
        (30, y - 28, "02"), (48, y - 28, "Mar"),
        (100, y - 28, "THOMAS"), (145, y - 28, "SABO"), (180, y - 28, "FELTHAM"),
        (405, y - 28, "67.18"), (545, y - 28, "20,394.70"),
        # dateless continuation (same 02 Mar)
        (100, y - 42, "ADIDAS"), (140, y - 42, "FELTHAM"),
        (405, y - 42, "72.70"), (545, y - 42, "20,322.00"),
        (30, y - 56, "06"), (48, y - 56, "Mar"),
        (100, y - 56, "Bank"), (128, y - 56, "Credit"), (163, y - 56, "TRG"),
        (462, y - 56, "3,100.80"), (545, y - 56, "23,422.80"),
        # footer with the statement date - the only full date in the doc
        (30, y - 90, "Statement"), (85, y - 90, "date"), (120, y - 90, "30"),
        (140, y - 90, "April"), (170, y - 90, "2020"),
    ]
    pdf = _make_pdf_positioned(tmp_path / "nationwide.pdf", rows)
    txns = list(PdfImporter().parse(pdf))
    assert len(txns) == 3
    assert txns[0].date == date(2020, 3, 2)   # year from statement date
    assert txns[0].amount == -67.18
    assert txns[1].date == date(2020, 3, 2)   # carried to the dateless row
    assert txns[1].amount == -72.70
    assert txns[2].amount == 3100.80          # £ In column
    assert not any("statement" in t.description.lower() for t in txns)


def test_undated_row_at_top_of_next_page_keeps_previous_date(tmp_path):
    """First Direct prints a day's date only on its first row. When that day
    continues onto the next page, the page's first transaction has no date -
    it must inherit the previous page's date, not be silently dropped (this
    lost a credit from a real statement)."""
    def header(y):
        return [
            (30, y, "Date"), (90, y, "Payment"), (140, y, "details"),
            (360, y, "Paidout"), (440, y, "Paidin"), (520, y, "Balance"),
        ]

    page1 = header(700) + [
        (30, 686, "01"), (50, 686, "Aug"), (80, 686, "26"),
        (105, 686, "Balance"), (150, 686, "brought"), (190, 686, "forward"),
        (520, 686, "120.00"),
        (30, 672, "21"), (50, 672, "Aug"), (80, 672, "26"),
        (105, 672, "CR"), (125, 672, "Client"), (440, 672, "1,480.52"),
        (90, 658, "Balancecarriedforward"), (520, 658, "1,600.52"),
    ]
    page2 = header(700) + [
        (90, 686, "Balancebroughtforward"), (520, 686, "1,600.52"),
        (105, 672, "CR"), (125, 672, "Client"), (440, 672, "1,433.47"),
        (520, 672, "3,033.99"),
        (30, 658, "24"), (50, 658, "Aug"), (80, 658, "26"),
        (105, 658, "SO"), (125, 658, "Donation"), (370, 658, "20.00"),
        (520, 658, "3,013.99"),
    ]
    pdf = _make_pdf_positioned_pages(tmp_path / "fd.pdf", [page1, page2])
    txns = list(PdfImporter().parse(pdf))
    assert [(t.date, t.amount) for t in txns] == [
        (date(2026, 8, 21), 1480.52),
        (date(2026, 8, 21), 1433.47),
        (date(2026, 8, 24), -20.00),
    ]
    assert txns[1].balance == 3033.99


def test_first_direct_two_line_descriptions_keep_payee_line(tmp_path):
    """First Direct wraps every transaction over two rows: the type and
    payee on the first ("02 Mar 26 SO ACME CHARITY TRUST"), the reference
    and amount on the second ("DONATION 50.00"). Dropping the figure-less
    first row lost the payee - and, because the description changed, let a
    re-imported statement slip past duplicate detection."""
    y = 700
    rows = [
        (30, y, "Date"), (90, y, "Payment"), (140, y, "details"),
        (360, y, "Paidout"), (440, y, "Paidin"), (520, y, "Balance"),
        (30, y - 14, "01"), (50, y - 14, "Mar"), (80, y - 14, "26"),
        (125, y - 14, "Balancebroughtforward"), (520, y - 14, "353.09"),
        # dated first row: type + payee, no figures
        (30, y - 28, "02"), (50, y - 28, "Mar"), (80, y - 28, "26"),
        (105, y - 28, "SO"), (125, y - 28, "ACME"), (165, y - 28, "CHARITY"),
        (220, y - 28, "TRUST"),
        (125, y - 42, "DONATION"), (370, y - 42, "50.00"),
        # same-day second transaction: undated first row
        (105, y - 56, "SO"), (125, y - 56, "J"), (140, y - 56, "SMITH"),
        (125, y - 70, "RENT"), (160, y - 70, "SHARE"), (370, y - 70, "1.80"),
        (520, y - 70, "301.29"),
        # single-row transaction
        (30, y - 84, "17"), (50, y - 84, "Mar"), (80, y - 84, "26"),
        (105, y - 84, "DD"), (125, y - 84, "EE"), (145, y - 84, "LIMITED"),
        (370, y - 84, "14.56"), (520, y - 84, "286.73"),
        (30, y - 98, "18"), (50, y - 98, "Mar"), (80, y - 98, "26"),
        (105, y - 98, "CR"), (125, y - 98, "A"), (140, y - 98, "OTHER"),
        (125, y - 112, "transfer"), (440, y - 112, "1,000.00"),
        (520, y - 112, "1,286.73"),
        (30, y - 126, "01"), (50, y - 126, "Apr"), (80, y - 126, "26"),
        (125, y - 126, "Balancecarriedforward"), (520, y - 126, "1,286.73"),
    ]
    pdf = _make_pdf_positioned(tmp_path / "fd.pdf", rows)
    txns = list(PdfImporter().parse(pdf))
    assert [(t.date, t.description, t.amount) for t in txns] == [
        (date(2026, 3, 2), "SO ACME CHARITY TRUST DONATION", -50.00),
        (date(2026, 3, 2), "SO J SMITH RENT SHARE", -1.80),
        (date(2026, 3, 17), "DD EE LIMITED", -14.56),
        (date(2026, 3, 18), "CR A OTHER transfer", 1000.00),
    ]
    # raw_data keeps both printed rows, as shown in duplicate prompts
    assert txns[0].raw_data == "02 Mar 26 SO ACME CHARITY TRUST\nDONATION 50.00"
