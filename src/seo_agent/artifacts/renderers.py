from __future__ import annotations

from io import BytesIO

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

from src.seo_agent.tasks.models import TaskPrdContent


class MarkdownPrdRenderer:
    key = "markdown"
    content_type = "text/markdown"

    def render(self, title: str, content: TaskPrdContent) -> str:
        sections = [
            f"# {title}",
            "",
            "## 任务背景",
            content.background,
            "",
            "## 目标搜索意图",
            content.target_intent,
            "",
            "## 目标关键词",
            *[f"- {item}" for item in content.target_keywords],
            "",
            "## 目标用户",
            content.target_audience,
            "",
            "## 竞品与内容缺口",
            content.competitor_gap,
            "",
            "## 内容类型",
            content.content_type,
            "",
            "## 内容结构",
            *[f"- {item}" for item in content.content_outline],
            "",
            "## 必须回答的问题",
            *[f"- {item}" for item in content.required_questions],
            "",
            "## SEO 元数据",
            f"- Title: {content.seo_title}",
            f"- Description: {content.seo_description}",
            "",
            "## 内部链接",
            *[f"- {item}" for item in content.internal_links],
            "",
            "## 禁止事项",
            *[f"- {item}" for item in content.prohibited_items],
            "",
            "## 交付要求",
            *[f"- {item}" for item in content.delivery_requirements],
            "",
            "## 验收标准",
            *[f"- {item}" for item in content.acceptance_criteria],
            "",
            "## 效果监控目标",
            *[f"- {item}" for item in content.monitoring_goals],
            "",
        ]
        return "\n".join(sections)


class PdfPrdRenderer:
    key = "pdf"
    content_type = "application/pdf"

    def __init__(self) -> None:
        try:
            pdfmetrics.getFont("STSong-Light")
        except KeyError:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

    def render(self, title: str, content: TaskPrdContent) -> bytes:
        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=16 * mm,
            bottomMargin=16 * mm,
            title=title,
        )
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "CJKTitle",
            parent=styles["Title"],
            fontName="STSong-Light",
            alignment=TA_LEFT,
        )
        heading_style = ParagraphStyle(
            "CJKHeading",
            parent=styles["Heading2"],
            fontName="STSong-Light",
        )
        body_style = ParagraphStyle(
            "CJKBody",
            parent=styles["BodyText"],
            fontName="STSong-Light",
            leading=16,
        )
        story = [Paragraph(title, title_style), Spacer(1, 8)]

        def section(name: str, value: str) -> None:
            story.extend(
                [Paragraph(name, heading_style), Paragraph(value or "—", body_style), Spacer(1, 6)]
            )

        def bullets(name: str, values: list[str]) -> None:
            story.append(Paragraph(name, heading_style))
            items = values or ["—"]
            story.append(
                ListFlowable(
                    [ListItem(Paragraph(str(item), body_style)) for item in items],
                    bulletType="bullet",
                )
            )
            story.append(Spacer(1, 6))

        section("任务背景", content.background)
        section("目标搜索意图", content.target_intent)
        bullets("目标关键词", content.target_keywords)
        section("目标用户", content.target_audience)
        section("竞品与内容缺口", content.competitor_gap)
        section("内容类型", content.content_type)
        bullets("内容结构", content.content_outline)
        bullets("必须回答的问题", content.required_questions)
        section("SEO Title", content.seo_title)
        section("SEO Description", content.seo_description)
        bullets("内部链接", content.internal_links)
        bullets("禁止事项", content.prohibited_items)
        bullets("交付要求", content.delivery_requirements)
        bullets("验收标准", content.acceptance_criteria)
        bullets("效果监控目标", [str(item) for item in content.monitoring_goals])
        document.build(story)
        return buffer.getvalue()
