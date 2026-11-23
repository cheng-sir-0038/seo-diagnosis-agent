"""Small, versioned excerpts from official graduate admissions catalogues.

Only the named programme/college row is represented. PDF table-cell line breaks
are normalized as spaces; the original text and locator remain visible.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    school: str
    year: int
    title: str
    url: str


@dataclass(frozen=True)
class Excerpt:
    chunk_id: str
    document: Source
    section: str
    topic: str
    quote: str


_WUST26 = Source(
    "武汉科技大学",
    2026,
    "武汉科技大学2026年硕士研究生招生专业目录",
    "https://xxgk.wust.edu.cn/__local/9/F7/E0/79A81A7F6A8287E0F59F08FD161_DB21FF5F_B398A.pdf",
)
_WUST25 = Source(
    "武汉科技大学",
    2025,
    "武汉科技大学2025年硕士研究生招生专业目录",
    "https://xxgk.wust.edu.cn/__local/3/35/6E/CFF90E015F02016557F4C049AD9_C99F0E22_66350.pdf",
)
_HBMZU26 = Source(
    "湖北民族大学",
    2026,
    "湖北民族大学医学部2026年硕士研究生招生专业目录",
    "https://www.hbmzu.edu.cn/__local/5/6C/3D/A7403CEB39FC1AC1A4C8F543EF2_7CB0C88B_1AC34.pdf?e=.pdf",
)
_HBMZU25 = Source(
    "湖北民族大学",
    2025,
    "湖北民族大学2025年硕士研究生招生专业目录",
    "https://xxgkw.hbmzu.edu.cn/xxgkw/info/1104/5281.htm",
)

PROGRAM = "临床医学专硕"
_WUST_ROW = "医学部第一临床学院／全日制内科学（105101）"
_HBMZU_ROW = "医学部／临床医学（105100）"
EXCERPTS = (
    Excerpt(
        "wust-2026-internal-prelim",
        _WUST26,
        f"{_WUST_ROW}／考试科目",
        "初试科目",
        "①101 思想政治理论 ②201 英语一 ③306 临床医学综合能力（西医） ④无",
    ),
    Excerpt(
        "wust-2026-internal-retest",
        _WUST26,
        f"{_WUST_ROW}／备注",
        "复试科目",
        "1. 英语测试 2. 临床技能测试（参考临床执业医师实践技能考试大纲） 3. 综合面试",
    ),
    Excerpt(
        "wust-2026-clinical-training",
        _WUST26,
        "招生专业目录说明／报考条件（三）3",
        "规培报考资格",
        "入学前正在规培或已获临床住院医师规范化培训合格证书人员不得报考临床医学硕士专业学位研究生。",
    ),
    Excerpt(
        "wust-2025-internal-prelim",
        _WUST25,
        "第一临床学院／全日制内科学（105101）／考试科目",
        "初试科目",
        "①101 思想政治理论 ②201 英语一 ③306 临床医学综合能力（西医） ④无",
    ),
    Excerpt(
        "hbmzu-2026-clinical-prelim",
        _HBMZU26,
        f"{_HBMZU_ROW}／初试科目",
        "初试科目",
        "①101 思想政治理论 ②201 英语一 ③306 临床医学综合能力（西医）",
    ),
    Excerpt(
        "hbmzu-2026-clinical-retest",
        _HBMZU26,
        f"{_HBMZU_ROW}／复试科目",
        "复试科目",
        "西医临床综合",
    ),
    Excerpt(
        "hbmzu-2026-clinical-training",
        _HBMZU26,
        f"{_HBMZU_ROW}／报考要求第3项",
        "规培报考资格",
        "不招收正在进行住院医师规范化培训的人员。",
    ),
    Excerpt(
        "hbmzu-2025-clinical-prelim",
        _HBMZU25,
        f"{_HBMZU_ROW}／考试科目",
        "初试科目",
        "①101思想政治理论②201英语（一）③306临床医学综合能力（西医）",
    ),
    Excerpt(
        "hbmzu-2025-clinical-retest",
        _HBMZU25,
        f"{_HBMZU_ROW}／复试科目",
        "复试科目",
        "西医临床综合",
    ),
)
