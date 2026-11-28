"""Project 3 -> Project 4 Campaign Intelligence & Recommendation Engine (Phase 9).

Encodes the empirical findings from Project 3 ('Pakistan Social Audience and
Campaign Intelligence Engine': 1,959 YouTube posts and 3,873 comments across
Sapphire, Khaadi, Gul Ahmed, Alkaram, and Sana Safinaz) directly into the
publisher workflow:

  1. Best posting windows (Asia/Karachi PKT peak engagement slots).
  2. Data-backed title & description helper (song/singer credit, celebrity
     mention, price disclosure, store/online availability, Spring Lawn hook).
  3. Built-in A/B test variant generator matching Project 3 Section 5.5
     recommendations (Short vs longer cut, price in description vs none,
     song credit vs none).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from publisher.auth import require_user
from publisher.db import get_db
from publisher.models import Product, User, Video, utcnow
from publisher.templating import templates
from publisher.timeutil import app_tz

router = APIRouter()


@dataclass(frozen=True)
class Project3Finding:
    area: str
    priority: str
    headline: str
    evidence: str
    action: str


# Directly grounded in Project 3 Final Report (Section 1 & Section 5.5)
PROJECT3_FINDINGS: tuple[Project3Finding, ...] = (
    Project3Finding(
        area="Content",
        priority="High",
        headline="Credit the song and singer in every description",
        evidence="34.4% of all audience comments (1,334 / 3,873) ask about the soundtrack; 353 explicitly request the song or singer name.",
        action="Include the track title, singer, and OST link in the first 3 lines of the YouTube description.",
    ),
    Project3Finding(
        area="Content",
        priority="High",
        headline="Name featured celebrities in the title and thumbnail",
        evidence="10.0% of comments (388) mention celebrity faces (e.g., Yumna Zaidi 122, Ayeza Khan 61, Ayeza/Aiza 47).",
        action="Place the model or celebrity name early in the video title and tags.",
    ),
    Project3Finding(
        area="Campaign",
        priority="High",
        headline="Lean into 'Spring Lawn' seasonal storytelling",
        evidence="Spring Lawn posts achieved the highest median like rate (1.09 across 69 posts, Kruskal-Wallis p < 0.0001).",
        action="Highlight seasonal collection names ('Spring Lawn', 'Unstitched Festive') in titles.",
    ),
    Project3Finding(
        area="Promotions",
        priority="High",
        headline="Disclose the starting price or price range upfront",
        evidence="123 comments ask about pricing; 91 simply write 'price?' due to missing price tags.",
        action="Add 'Starting from PKR ...' or the 3-piece unstitched price in the description.",
    ),
    Project3Finding(
        area="Customer Service",
        priority="High",
        headline="Make store & online availability obvious",
        evidence="121 comments ask about stock/stores; 23 ask directly where to buy.",
        action="Include the direct product URL, launch date, and nationwide delivery note.",
    ),
    Project3Finding(
        area="Format & A/B Testing",
        priority="Medium",
        headline="Test Shorts vs. longer cuts and description hooks",
        evidence="92% of brand posts are Shorts (median like rate 0.62 vs 0.76 for longer cuts, p = 0.1076).",
        action="Schedule A/B variants (e.g., Variant A: Price + Song Credit vs Variant B: Standard) and compare stats.",
    ),
)

# Recommended PKT posting windows for Pakistani women's fashion/lawn audiences
RECOMMENDED_SLOTS_PKT: tuple[dict, ...] = (
    {
        "label": "Prime Evening Browse (8:30 PM PKT)",
        "hour": 20,
        "minute": 30,
        "reason": "Peak after-dinner mobile YouTube Shorts & Lawn lookbook traffic in Pakistan.",
    },
    {
        "label": "Afternoon Tea & Scroll (4:30 PM PKT)",
        "hour": 16,
        "minute": 30,
        "reason": "High engagement window before evening shopping and store visits.",
    },
    {
        "label": "Weekend Brunch Launch (12:30 PM PKT)",
        "hour": 12,
        "minute": 30,
        "reason": "Ideal for weekend collection drops and sale announcements.",
    },
)

AB_TEST_TEMPLATES: tuple[dict, ...] = (
    {
        "code": "P3-TEST-1",
        "name": "Price Disclosure Test",
        "variant_a": "Variant A: Price in Description",
        "variant_b": "Variant B: No Price (Curiosity Hook)",
        "hypothesis": "Reduces repetitive 'price?' comments (91 in Project 3) and increases click-through intent.",
    },
    {
        "code": "P3-TEST-2",
        "name": "Soundtrack Credit Test",
        "variant_a": "Variant A: Song & Singer Credited",
        "variant_b": "Variant B: No Soundtrack Credit",
        "hypothesis": "Captures the 34.4% music-curious audience segment in search and retention.",
    },
    {
        "code": "P3-TEST-3",
        "name": "Cut Length Test",
        "variant_a": "Variant A: Vertical Short (<60s)",
        "variant_b": "Variant B: Full Lookbook Cut (>60s)",
        "hypothesis": "Tests whether higher median like rate of longer cuts (0.76 vs 0.62) holds for your catalog.",
    },
)


def next_recommended_slots(now_utc: datetime | None = None) -> list[dict]:
    """Return the next upcoming recommended PKT datetime-local strings."""
    now_utc = now_utc or utcnow()
    tz = app_tz()
    local_now = now_utc.astimezone(tz)
    suggestions: list[dict] = []

    for slot in RECOMMENDED_SLOTS_PKT:
        candidate = local_now.replace(
            hour=slot["hour"], minute=slot["minute"], second=0, microsecond=0
        )
        if candidate <= local_now + timedelta(minutes=15):
            candidate = candidate + timedelta(days=1)
        suggestions.append(
            {
                "label": slot["label"],
                "reason": slot["reason"],
                "input_value": candidate.strftime("%Y-%m-%dT%H:%M"),
                "display": candidate.strftime("%d %b %Y, %I:%M %p PKT"),
            }
        )
    return suggestions


def generate_optimized_copy(
    product_name: str,
    product_description: str = "",
    campaign: str = "Spring Lawn",
    celebrity: str = "",
    song_credit: str = "",
    price_pkr: str = "",
    availability_url: str = "",
    variant: str = "A",
) -> dict:
    """Build YouTube-ready title, description, and tags using Project 3 rules."""
    clean_name = (product_name or "Unstitched Lawn Collection").strip()
    clean_campaign = (campaign or "Spring Lawn").strip()
    clean_celeb = celebrity.strip()
    clean_song = song_credit.strip()
    clean_price = price_pkr.strip()
    clean_url = availability_url.strip()

    # Title construction (<= 100 chars, celebrity + campaign + product)
    title_parts = [clean_name]
    if clean_celeb:
        title_parts.append(f"ft. {clean_celeb}")
    if clean_campaign:
        title_parts.append(f"| {clean_campaign} 2026")
    title = " ".join(title_parts)[:100]

    lines: list[str] = []
    if product_description.strip():
        lines.append(product_description.strip())
    else:
        lines.append(
            f"Discover {clean_name} from our {clean_campaign} collection — crafted for effortless elegance."
        )

    lines.append("")
    # Variant B omits price for A/B testing ("Price in description vs none")
    if clean_price and variant.upper() != "B":
        lines.append(f"Price: PKR {clean_price} (3-Piece Unstitched)")
    elif not clean_price and variant.upper() != "B":
        lines.append("Price: Check product link below for latest pricing & festive offers.")

    if clean_url:
        lines.append(f"Shop Online & Store Availability: {clean_url}")
    else:
        lines.append("Availability: Available online and in flagship stores nationwide.")

    if clean_song:
        lines.append(f"Soundtrack / OST: {clean_song}")

    lines.append("Delivery & Care: Nationwide delivery in 2-4 working days. Dry clean / gentle wash recommended.")

    tags = [
        clean_name.lower(),
        clean_campaign.lower(),
        "pakistani fashion",
        "lawn suit 2026",
        "unstitched lawn",
    ]
    if clean_celeb:
        tags.append(clean_celeb.lower())

    return {
        "title": title,
        "description": "\n".join(lines)[:4900],
        "tags": ", ".join(dict.fromkeys(tags))[:480],
        "variant_label": f"Variant {variant.upper()}",
    }


@router.get("/insights", response_class=HTMLResponse)
def insights_page(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    products = list(
        db.scalars(
            select(Product).where(Product.user_id == user.id).order_by(Product.name)
        ).all()
    )
    videos = list(
        db.scalars(
            select(Video).where(Video.user_id == user.id).order_by(Video.id.desc())
        ).all()
    )
    generated = None
    if products:
        p = products[0]
        generated = generate_optimized_copy(p.name, p.description)

    return templates.TemplateResponse(
        request=request,
        name="insights.html",
        context={
            "findings": PROJECT3_FINDINGS,
            "slots": next_recommended_slots(),
            "ab_tests": AB_TEST_TEMPLATES,
            "products": products,
            "videos": videos,
            "generated": generated,
        },
    )


@router.post("/insights/generate")
def insights_generate_api(
    product_name: str = Form(""),
    product_description: str = Form(""),
    campaign: str = Form("Spring Lawn"),
    celebrity: str = Form(""),
    song_credit: str = Form(""),
    price_pkr: str = Form(""),
    availability_url: str = Form(""),
    variant: str = Form("A"),
    user: User = Depends(require_user),
):
    result = generate_optimized_copy(
        product_name=product_name,
        product_description=product_description,
        campaign=campaign,
        celebrity=celebrity,
        song_credit=song_credit,
        price_pkr=price_pkr,
        availability_url=availability_url,
        variant=variant,
    )
    return JSONResponse(result)
