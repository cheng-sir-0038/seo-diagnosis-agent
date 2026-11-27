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
    result = prepare_case([material])
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
    result = prepare_case([material])
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
    result = prepare_case([material])
    assert not result["comparisons"]
    assert [issue["source"] for issue in result["issues"]] == [
        "synthetic.csv · 第 2 行", "synthetic.csv · 第 3 行",
    ]
    assert all("同一项目" in issue["text"] for issue in result["issues"])


def test_case_review_retains_original_provenance_in_a_legacy_save():
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
    [comparison] = prepare_case(case.files)["comparisons"]
    assert comparison["delta"] == "-0.5"

    legacy_draft = {
        "revision": 2, "comparisons": [comparison], "issues": [], "plans": [], "pending_plans": [],
    }
    legacy_saved = {"revision": 2, "saved_at": "2027-01-06T08:00:00+00:00", "tasks": []}
    case.revision, case.draft, case.saved = 2, legacy_draft, legacy_saved
    historical = CaseData.model_validate_json(case.model_dump_json()).view()
    assert historical["draft"] == legacy_draft and historical["saved"] == legacy_saved
    original = historical["records"][1]
    assert original["id"] == comparison["later"]
    assert original["source"] == "synthetic.csv · 第 3 行"
    assert original["original"]["method"] is None


def test_one_import_supplies_a_sourced_checklist():
    material = read_material("notes.csv", (
        "subject_id,kind,item,record_date,note,due_date,plan_status\n"
        "CASE-01,visit_summary,Last visit summary,2027-01-01,Previous visit note,,\n"
        "CASE-01,visit_plan,Bring originals,2027-01-01,Explicit plan,,unknown\n"
        "CASE-01,purchase,Product A,2027-01-01,Receipt only,,\n"
        "CASE-01,visit_plan,Follow-up appointment,2027-01-02,Already booked,,unknown\n"
        "CASE-01,visit_plan,Earlier appointment,2027-01-02,Attended,,documented_done\n"
        "CASE-01,self_report,Daily notes,,Diary entry without a date,,\n"
    ).encode(), "CASE-01")
    result = prepare_case([material])
    assert result == prepare_case([material])
    assert result["comparisons"] == [] and result["issues"] == []
    checklist = result["checklist"]
    [bring] = checklist["materials_to_bring"]
    assert bring["title"] == "notes.csv" and len(bring["sources"]) == 6
    plan, appointment, completed = checklist["existing_arrangements"]
    assert plan["title"] == "Bring originals" and "gaps" not in plan
    assert appointment["title"] == "Follow-up appointment" and "due_date" not in appointment
    assert "预约日期" in appointment["gaps"][0]
    assert completed["plan_status"] == "documented_done" and "gaps" not in completed
    questions = checklist["questions_for_doctor"]
    assert all(
        "Product A" not in item["title"] and item["title"].endswith("？") for item in questions
    )
    [notes] = [item for item in questions if "Daily notes" in item["title"]]
    assert "未注明日期" in notes["gaps"][0]
    assert result["pending_plans"] == [
        item["sources"][0]["record_id"] for item in (plan, appointment)
    ]
    sources = {entry.id: entry.source for entry in material.evidence}
    for section in checklist.values():
        for item in section:
            assert item["sources"]
            assert all(
                source["source"] == sources[source["record_id"]] for source in item["sources"]
            )


def test_checklist_links_each_usable_file_even_with_duplicate_records():
    content = b"subject_id,kind,item\nCASE-01,visit_summary,Prior visit\n"
    files = [
        read_material("first.csv", content, "CASE-01"),
        read_material("second.csv", b"\xef\xbb\xbf" + content, "CASE-01"),
        read_material("unreadable.csv", b"missing,headers\n", "CASE-01"),
    ]
    assert len(case_evidence(files)) == 1
    case = CaseData(files=files, draft=prepare_case(files)).view()
    brings = case["draft"]["checklist"]["materials_to_bring"]
    assert [item["title"] for item in brings] == ["first.csv", "second.csv"]
    record_ids = [row["id"] for row in case["records"]]
    assert [item["sources"][0]["record_id"] for item in brings] == record_ids
