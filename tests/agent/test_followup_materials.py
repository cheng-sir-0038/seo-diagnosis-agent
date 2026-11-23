from io import BytesIO

from reportlab.pdfgen.canvas import Canvas

from src.followup_agent.materials import case_evidence, prepare_case, read_material


def test_text_pdf_reads_rows_with_page_provenance_and_treats_missing_date_as_unknown():
    output = BytesIO()
    pdf = Canvas(output)
    pdf.drawString(25, 800, "subject_id|kind|item|record_date|value|unit|specimen|method")
    pdf.drawString(25, 775, "CASE-01|report|Glucose||5.9|mmol/L|plasma|assay")
    pdf.save()
    material = read_material("report.pdf", output.getvalue(), "CASE-01")
    assert material.error is None
    [entry] = material.evidence
    assert entry.source == "report.pdf · 第 1 页第 2 行"
    assert entry.record.record_date is None
    assert entry.record.value == "5.9"
    result = prepare_case(case_evidence([material]))
    assert not result["comparisons"]
    assert result["issues"][0]["text"] == "记录日期待核对"


def test_missing_schema_and_pdf_scans_are_visible_as_failed_materials():
    csv_material = read_material("missing.csv", b"kind,item\nreport,Value\n", "CASE-01")
    assert csv_material.error and not csv_material.evidence
    output = BytesIO()
    pdf = Canvas(output)
    pdf.showPage()
    pdf.save()
    scanned = read_material("scan.pdf", output.getvalue(), "CASE-01")
    assert scanned.error and not scanned.evidence
