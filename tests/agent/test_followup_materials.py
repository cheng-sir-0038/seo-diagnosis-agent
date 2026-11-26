from io import BytesIO

from reportlab.pdfgen.canvas import Canvas

from src.followup_agent.materials import case_evidence, prepare_case, read_material
from src.followup_agent.store import CaseData


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


def test_latest_comparable_dated_pair_ignores_ambiguous_and_unmatched_rows():
    material = read_material("synthetic.csv", (
        "subject_id,kind,item,record_date,value,unit,specimen,method\n"
        "CASE-01,report,Measure,2027-01-03,5.4,U,plasma,assay\n"
        "CASE-01,report,Measure,,50,U,plasma,assay\n"
        "CASE-01,report,Measure,2027-01-01,5.9,U,plasma,assay\n"
        "CASE-01,report,Measure,2027-01-04,4.4,U,plasma,other\n"
        "CASE-01,report,Measure,2027-01-05,5.2,U,plasma,assay\n"
        "CASE-01,report,Measure,2027-01-05,5.1,U,plasma,assay\n"
    ).encode(), "CASE-01")
    evidence = case_evidence([material])
    by_id = {entry.id: entry for entry in evidence}
    result = prepare_case(evidence)
    [comparison] = result["comparisons"]
    assert by_id[comparison["earlier"]].record.value == "5.9"
    assert by_id[comparison["later"]].record.value == "5.4"
    assert comparison["delta"] == "-0.5" and comparison["reasons"] == []
    issues = {issue["source"]: issue["text"] for issue in result["issues"]}
    assert "方法缺失或不同" in issues["synthetic.csv · 第 5 行"]
    assert "同一天有多条" in issues["synthetic.csv · 第 6 行"]
    assert issues["synthetic.csv · 第 3 行"] == "记录日期待核对"


def test_unrelated_dated_items_are_not_a_report_pair():
    material = read_material("synthetic.csv", (
        "subject_id,kind,item,record_date,value,unit,specimen,method\n"
        "CASE-01,report,First,2027-01-01,5.9,U,plasma,assay\n"
        "CASE-01,report,Second,2027-01-03,5.4,U,plasma,assay\n"
    ).encode(), "CASE-01")
    result = prepare_case(case_evidence([material]))
    assert not result["comparisons"]
    assert [issue["source"] for issue in result["issues"]] == [
        "synthetic.csv · 第 2 行", "synthetic.csv · 第 3 行",
    ]
    assert all("同一项目" in issue["text"] for issue in result["issues"])


def test_case_review_rechecks_source_mismatch_after_a_correction():
    material = read_material("synthetic.csv", (
        "subject_id,kind,item,record_date,value,unit,specimen,method\n"
        "CASE-01,report,Measure,2027-01-01,5.9,U,plasma,assay\n"
        "CASE-01,report,Measure,2027-01-03,5.4,U,plasma,\n"
    ).encode(), "CASE-01")
    case = CaseData(files=[material])
    review = case.view()["report_review"]
    assert review["comparable_pairs"] == 0
    [issue] = review["issues"]
    assert "方法缺失或不同" in issue["text"]
    first, second = case.files[0].evidence
    assert issue["source"] == f"{first.source} / {second.source}"

    second.original = second.record
    second.record = second.record.model_copy(update={"method": "assay"})
    view = case.view()
    assert view["report_review"] == {"comparable_pairs": 1, "issues": []}
    assert view["records"][1]["original"]["method"] is None
    [comparison] = prepare_case(case_evidence(case.files))["comparisons"]
    assert comparison["delta"] == "-0.5"


def test_existing_plans_and_purchase_records_do_not_create_report_comparison_prompts():
    material = read_material("synthetic.csv", (
        "subject_id,kind,item,record_date,value\n"
        "CASE-01,visit_plan,Bring originals,2027-01-01,\n"
        "CASE-01,purchase,Product A,2027-01-03,1\n"
    ).encode(), "CASE-01")
    evidence = case_evidence([material])
    assert prepare_case(evidence) == {
        "comparisons": [], "issues": [],
        "plans": [evidence[0].id], "pending_plans": [evidence[0].id],
    }
