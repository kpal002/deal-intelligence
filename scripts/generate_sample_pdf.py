"""Generate a realistic 10-page mock PE teaser PDF for the demo.

The deck (NorthStar Logistics, a fictional B2B SaaS company) contains narrative
claims, a financial table, and team bios — the three content shapes the pipeline
must handle. Cue phrases (ARR, TAM, team size, geography) are written in clear
sentences so the offline mock extractor produces meaningful facts; the real
Claude extractor handles arbitrary phrasing.

Run:
    python scripts/generate_sample_pdf.py [output_path]
"""

from __future__ import annotations

import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

DEFAULT_OUTPUT = "data/sample_pitch_deck.pdf"


def _story() -> list:
    """Build the flowable story for the mock deck.

    Returns:
        A list of reportlab flowables, one logical section per page.
    """
    styles = getSampleStyleSheet()
    h1 = styles["Title"]
    body = styles["BodyText"]
    story: list = []

    def page(title: str, paragraphs: list[str]) -> None:
        story.append(Paragraph(title, h1))
        story.append(Spacer(1, 0.2 * inch))
        for para in paragraphs:
            story.append(Paragraph(para, body))
            story.append(Spacer(1, 0.1 * inch))
        story.append(PageBreak())

    # 1. Cover
    page(
        "NorthStar Logistics",
        [
            "Series A Investment Teaser",
            "CONFIDENTIAL — for the intended recipient only. This document "
            "contains forward-looking statements.",
        ],
    )

    # 2. Executive summary
    page(
        "Executive Summary",
        [
            "NorthStar Logistics is a B2B SaaS platform that automates "
            "freight brokerage operations for mid-market carriers.",
            "Headquartered in the United States (San Francisco, California), "
            "the company serves customers across North America.",
            "The business reached an ARR of $4.2M in the most recent quarter.",
        ],
    )

    # 3. Market
    page(
        "Market Opportunity",
        [
            "The total addressable market (TAM) for freight automation software "
            "is estimated at $12B globally.",
            "Digitization of logistics back-office workflows is still early, "
            "leaving substantial room for category leaders to emerge.",
        ],
    )

    # 4. Traction
    page(
        "Traction",
        [
            "NorthStar Logistics now serves 240 paying customers.",
            "Net new revenue has been growing 18% MoM over the trailing two "
            "quarters, driven by expansion within existing accounts.",
            "Gross retention remains strong as carriers embed the platform into "
            "daily dispatch operations.",
        ],
    )

    # 5. Financials (table)
    story.append(Paragraph("Financial Summary", h1))
    story.append(Spacer(1, 0.2 * inch))
    story.append(
        Paragraph(
            "The table below summarizes annual recurring revenue and gross "
            "margin for NorthStar Logistics over the last three fiscal years.",
            body,
        )
    )
    story.append(Spacer(1, 0.15 * inch))
    table_data = [
        ["Metric", "FY2023", "FY2024", "FY2025"],
        ["ARR", "$1.1M", "$2.6M", "$4.2M"],
        ["Gross Margin", "71%", "74%", "78%"],
        ["Customers", "60", "150", "240"],
    ]
    table = Table(table_data, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ]
        )
    )
    story.append(table)
    story.append(PageBreak())

    # 6. Funding
    page(
        "Funding History",
        [
            "NorthStar Logistics raised $6M in Series A financing led by a "
            "specialist logistics-technology fund.",
            "Prior to that, the company raised $1.5M in seed capital from "
            "angel investors and operators in the freight industry.",
        ],
    )

    # 7. Team
    page(
        "Team",
        [
            "NorthStar Logistics is run by a team of 14 across engineering, "
            "operations, and go-to-market.",
            "The founding team previously built dispatch software at a national "
            "carrier and brings deep domain expertise.",
            "Leadership includes former operators from established logistics "
            "technology companies.",
        ],
    )

    # 8. Competition
    page(
        "Competitive Landscape",
        [
            "NorthStar competes with legacy transportation management systems "
            "and a handful of point solutions.",
            "Its differentiation is an integrated B2B SaaS workflow that "
            "replaces spreadsheets and disconnected tools.",
        ],
    )

    # 9. Roadmap
    page(
        "Roadmap",
        [
            "The product roadmap prioritizes automated carrier matching and "
            "predictive capacity planning.",
            "Planned expansion targets adjacent mid-market segments over the "
            "next 18 months.",
        ],
    )

    # 10. Disclaimer (legal boilerplate)
    page(
        "Disclaimer",
        [
            "This teaser is provided for informational purposes only and does "
            "not constitute an offer to sell securities.",
            "Forward-looking statements are subject to risks and uncertainties. "
            "Recipients should conduct their own due diligence. CONFIDENTIAL.",
        ],
    )

    return story


def generate(output_path: str = DEFAULT_OUTPUT) -> None:
    """Render the mock deck to a PDF file.

    Args:
        output_path: Destination path for the generated PDF.
    """
    doc = SimpleDocTemplate(output_path, pagesize=LETTER)
    doc.build(_story())
    print(f"Wrote sample deck to {output_path}")


if __name__ == "__main__":
    generate(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUTPUT)
