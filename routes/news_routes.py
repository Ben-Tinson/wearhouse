from datetime import datetime, time
import math
import re
from typing import Any, Dict, Optional
import uuid
import os

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.datastructures import CombinedMultiDict, MultiDict
from werkzeug.utils import secure_filename

from decorators import admin_required
from extensions import db
from forms import ArticleForm, EmptyForm
from models import Article, ArticleBlock, SiteSchema
from services.news_service import normalise_tags, parse_tags, slugify
import json
from utils import allowed_file


news_bp = Blueprint("news", __name__)

BLOCK_LIMIT = 30
BLOCK_TYPES = [
    ("heading", "Heading"),
    ("body", "Body"),
    ("side_image", "Side Image"),
    ("image", "Full Image"),
    ("carousel", "Image Carousel"),
]
ALIGN_CHOICES = ["left", "right", "full"]


def _clean_json_payload(raw: Optional[str], previous: Optional[str] = None) -> Optional[str]:
    if not raw or not raw.strip():
        return None if previous is None else previous
    try:
        json.loads(raw)
    except json.JSONDecodeError:
        flash("Invalid JSON-LD provided. Please check the schema markup.", "warning")
        return previous
    return raw.strip()


def _update_global_schema_from_request() -> None:
    org_raw = (request.form.get("global_org_schema") or "").strip()
    site_raw = (request.form.get("global_site_schema") or "").strip()
    if org_raw:
        cleaned = _clean_json_payload(org_raw)
        if cleaned:
            _upsert_site_schema("organization", cleaned)
    if site_raw:
        cleaned = _clean_json_payload(site_raw)
        if cleaned:
            _upsert_site_schema("website", cleaned)


def _upsert_site_schema(schema_type: str, json_text: str) -> None:
    record = SiteSchema.query.filter_by(schema_type=schema_type).first()
    if not record:
        record = SiteSchema(schema_type=schema_type, json_text=json_text)
        db.session.add(record)
    else:
        record.json_text = json_text


def _publisher_from_schema(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        raw = ""
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    name = data.get("name") or "Soletrak"
    logo = data.get("logo")
    url = data.get("url")
    publisher = {"@type": "Organization", "name": name}
    if isinstance(logo, dict) and logo.get("url"):
        publisher["logo"] = {"@type": "ImageObject", "url": logo.get("url")}
    elif isinstance(logo, str):
        publisher["logo"] = {"@type": "ImageObject", "url": logo}
    else:
        publisher["logo"] = {
            "@type": "ImageObject",
            "url": url_for("static", filename="brand/soletrak-logo.svg", _external=True),
        }
    if url:
        publisher["url"] = url
    return publisher


def _build_article_schema(article: Article, publisher: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    headline = article.meta_title or article.title
    image_url = article.hero_image_url
    if image_url and not image_url.startswith("http"):
        image_url = url_for("main.uploaded_file", filename=image_url, _external=True)
    author_name = article.author_name or "Soletrak"
    schema = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": headline,
        "datePublished": article.published_at.isoformat() if article.published_at else None,
        "dateModified": article.updated_at.isoformat() if article.updated_at else None,
        "author": {"@type": "Person", "name": author_name},
        "publisher": publisher or {"@type": "Organization", "name": "Soletrak"},
        "image": [image_url] if image_url else None,
        "mainEntityOfPage": {
            "@type": "WebPage",
            "@id": article.canonical_url or url_for("news.news_detail", slug=article.slug, _external=True),
        },
    }
    return {k: v for k, v in schema.items() if v is not None}


def _article_is_published(article: Article) -> bool:
    return bool(article.published_at and article.published_at <= datetime.utcnow())


def _ensure_unique_slug(raw_slug: str, article_id: int = None) -> str:
    base = slugify(raw_slug)
    if not base:
        base = "article"
    slug = base
    suffix = 2
    while True:
        query = Article.query.filter_by(slug=slug)
        if article_id:
            query = query.filter(Article.id != article_id)
        if not query.first():
            return slug
        slug = f"{base}-{suffix}"
        suffix += 1


def _save_uploaded_image(image_file) -> str:
    filename = secure_filename(image_file.filename)
    extension = os.path.splitext(filename)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}{extension}"
    save_path = os.path.join(current_app.config["UPLOAD_FOLDER"], unique_filename)
    image_file.save(save_path)
    return unique_filename


def _parse_publish_date(raw_value: str):
    if not raw_value:
        return None
    raw_value = raw_value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw_value, fmt).date()
        except ValueError:
            continue
    return None


def _normalise_published_formdata(formdata):
    data = MultiDict(formdata)
    raw_value = (data.get("published_at") or "").strip()
    if not raw_value:
        data.pop("published_at", None)
        return data
    if "T" in raw_value:
        raw_value = raw_value.split("T", 1)[0]
    if " " in raw_value:
        raw_value = raw_value.split(" ", 1)[0]
    data.setlist("published_at", [raw_value])
    return data


def _collect_block_slots(article: Article = None):
    slots = []
    existing = {block.position: block for block in (article.blocks if article else [])}
    for position in range(1, BLOCK_LIMIT + 1):
        block = existing.get(position)
        carousel_images = []
        carousel_alts_text = ""
        if block and block.carousel_images_json:
            try:
                carousel_images = json.loads(block.carousel_images_json) or []
            except json.JSONDecodeError:
                carousel_images = []
            if isinstance(carousel_images, list):
                carousel_alts_text = "\n".join([item.get("alt", "") for item in carousel_images if isinstance(item, dict)])
        slots.append({
            "position": position,
            "block_type": block.block_type if block else "",
            "heading_text": block.heading_text if block else "",
            "heading_level": block.heading_level if block else "",
            "body_text": block.body_text if block else "",
            "image_url": block.image_url if block else "",
            "image_alt": block.image_alt if block else "",
            "caption": block.caption if block else "",
            "align": block.align if block else "",
            "carousel_images": carousel_images,
            "carousel_alts_text": carousel_alts_text,
        })
    return slots


def _apply_blocks_from_request(article: Article):
    if article.id:
        ArticleBlock.query.filter_by(article_id=article.id).delete()
        db.session.flush()
    else:
        article.blocks.clear()
    for position in range(1, BLOCK_LIMIT + 1):
        block_type = (request.form.get(f"block_type_{position}") or "").strip()
        if not block_type:
            continue
        heading_text = (request.form.get(f"block_heading_{position}") or "").strip()
        body_text = (request.form.get(f"block_body_{position}") or "").strip()
        caption = (request.form.get(f"block_caption_{position}") or "").strip()
        align = (request.form.get(f"block_align_{position}") or "").strip().lower()
        if align and align not in ALIGN_CHOICES:
            align = None
        heading_level = (request.form.get(f"block_heading_level_{position}") or "").strip().lower()
        if heading_level not in {"h2", "h3"}:
            heading_level = "h2"
        image_alt = (request.form.get(f"block_image_alt_{position}") or "").strip()

        image_url = None
        image_file = request.files.get(f"block_image_file_{position}")
        if image_file and image_file.filename:
            if allowed_file(image_file.filename):
                image_url = _save_uploaded_image(image_file)
            else:
                flash(f"Block {position}: invalid image file type.", "warning")
        else:
            existing_image = (request.form.get(f"block_image_existing_{position}") or "").strip()
            if existing_image:
                image_url = existing_image

        carousel_images = []
        if block_type == "carousel":
            existing_raw = (request.form.get(f"block_carousel_existing_{position}") or "").strip()
            if existing_raw:
                try:
                    carousel_images = json.loads(existing_raw) or []
                except json.JSONDecodeError:
                    carousel_images = []
            files = request.files.getlist(f"block_carousel_files_{position}")
            for file in files:
                if not file or not file.filename:
                    continue
                if allowed_file(file.filename):
                    carousel_images.append({"url": _save_uploaded_image(file), "alt": ""})
                else:
                    flash(f"Block {position}: invalid carousel image file type.", "warning")
            alts_raw = (request.form.get(f"block_carousel_alts_{position}") or "").strip()
            if alts_raw and isinstance(carousel_images, list):
                alt_lines = [line.strip() for line in alts_raw.splitlines() if line.strip()]
                for idx, alt in enumerate(alt_lines):
                    if idx < len(carousel_images) and isinstance(carousel_images[idx], dict):
                        carousel_images[idx]["alt"] = alt

        if block_type == "heading" and not heading_text:
            continue
        if block_type == "body" and not body_text:
            continue
        if block_type in {"side_image", "image"} and not image_url:
            continue
        if block_type == "carousel" and not carousel_images:
            continue

        article.blocks.append(ArticleBlock(
            position=position,
            block_type=block_type,
            heading_text=heading_text or None,
            heading_level=heading_level if block_type == "heading" else None,
            body_text=body_text or None,
            image_url=image_url or None,
            image_alt=image_alt or None,
            caption=caption or None,
            align=align or None,
            carousel_images_json=json.dumps(carousel_images) if block_type == "carousel" else None,
        ))


@news_bp.route("/news")
def news_index():
    page = max(1, int(request.args.get("page", 1)))
    per_page = 12
    brand = (request.args.get("brand") or "").strip()
    tag = (request.args.get("tag") or "").strip()
    sort = (request.args.get("sort") or "newest").strip()
    search = (request.args.get("q") or "").strip()

    is_admin = current_user.is_authenticated and current_user.is_admin
    query = Article.query
    if not is_admin:
        query = query.filter(Article.published_at.isnot(None))
        query = query.filter(Article.published_at <= datetime.utcnow())

    if brand:
        query = query.filter(Article.brand == brand)
    if tag:
        query = query.filter(Article.tags.ilike(f"%{tag}%"))
    if search:
        query = query.filter(Article.title.ilike(f"%{search}%"))

    if sort == "oldest":
        query = query.order_by(Article.published_at.asc())
    else:
        sort = "newest"
        query = query.order_by(Article.published_at.desc())

    total_count = query.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page) if total_count else 1
    page = min(page, total_pages)
    articles = query.offset((page - 1) * per_page).limit(per_page).all()

    brand_query = db.session.query(Article.brand).filter(Article.brand.isnot(None))
    if not is_admin:
        brand_query = brand_query.filter(Article.published_at.isnot(None))
        brand_query = brand_query.filter(Article.published_at <= datetime.utcnow())
    brands = [
        row[0] for row in brand_query.distinct().order_by(Article.brand.asc()).all()
    ]
    tags = set()
    tag_query = db.session.query(Article.tags).filter(Article.tags.isnot(None))
    if not is_admin:
        tag_query = tag_query.filter(Article.published_at.isnot(None))
        tag_query = tag_query.filter(Article.published_at <= datetime.utcnow())
    for row in tag_query.all():
        tags.update(parse_tags(row[0]))
    tag_list = sorted(tags)

    return render_template(
        "news/index.html",
        title="News",
        articles=articles,
        brands=brands,
        tags=tag_list,
        selected_brand=brand,
        selected_tag=tag,
        selected_sort=sort,
        search_query=search,
        page=page,
        total_pages=total_pages,
        is_admin=is_admin,
    )


@news_bp.route("/news/<slug>")
def news_detail(slug):
    article = Article.query.filter_by(slug=slug).first()
    if not article:
        abort(404)
    if not _article_is_published(article) and not (current_user.is_authenticated and current_user.is_admin):
        abort(404)
    global_org = SiteSchema.query.filter_by(schema_type="organization").first()
    global_site = SiteSchema.query.filter_by(schema_type="website").first()
    publisher = _publisher_from_schema(global_org.json_text if global_org else None)
    article_schema = _build_article_schema(article, publisher=publisher)
    author_schema = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": article.author_name or "Soletrak",
    }
    if article.author_bio:
        author_schema["description"] = article.author_bio
    if article.author_image_url:
        author_image = article.author_image_url
        if not author_image.startswith("http"):
            author_image = url_for("main.uploaded_file", filename=author_image, _external=True)
        author_schema["image"] = author_image
    breadcrumb_schema = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": 1,
                "name": "Home",
                "item": url_for("main.home", _external=True),
            },
            {
                "@type": "ListItem",
                "position": 2,
                "name": "News",
                "item": url_for("news.news_index", _external=True),
            },
            {
                "@type": "ListItem",
                "position": 3,
                "name": article.title,
                "item": article.canonical_url or url_for("news.news_detail", slug=article.slug, _external=True),
            },
        ],
    }
    is_admin = current_user.is_authenticated and current_user.is_admin
    article_tags = set(parse_tags(article.tags or ""))
    # Estimated reading time (roughly 200 wpm)
    text_parts = [article.excerpt or ""]
    for block in article.blocks:
        if block.block_type == "body" and block.body_text:
            text_parts.append(block.body_text)
        elif block.block_type == "heading" and block.heading_text:
            text_parts.append(block.heading_text)
    combined_text = " ".join(text_parts)
    word_count = len(re.findall(r"\b\w+\b", combined_text))
    reading_time_minutes = max(1, math.ceil(word_count / 200)) if word_count else 1

    # Jump links for headings
    heading_links = []
    used_ids = set()
    for block in article.blocks:
        if block.block_type != "heading" or not block.heading_text:
            continue
        base_id = slugify(block.heading_text)
        if not base_id:
            base_id = f"section-{block.id}"
        anchor_id = base_id
        suffix = 2
        while anchor_id in used_ids:
            anchor_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(anchor_id)
        setattr(block, "anchor_id", anchor_id)
        heading_links.append(
            {
                "id": anchor_id,
                "text": block.heading_text,
                "level": block.heading_level or "h2",
            }
        )

    for block in article.blocks:
        if block.block_type != "carousel":
            continue
        carousel_images = []
        if block.carousel_images_json:
            try:
                carousel_images = json.loads(block.carousel_images_json) or []
            except json.JSONDecodeError:
                carousel_images = []
        if not isinstance(carousel_images, list):
            carousel_images = []
        setattr(block, "carousel_images", carousel_images)

    related_query = Article.query.filter(Article.id != article.id)
    if not is_admin:
        related_query = related_query.filter(Article.published_at.isnot(None))
    related_candidates = related_query.order_by(Article.published_at.desc().nullslast(), Article.id.desc()).limit(20).all()

    def _related_score(candidate: Article) -> int:
        score = 0
        if article.brand and candidate.brand and article.brand.strip().lower() == candidate.brand.strip().lower():
            score += 2
        candidate_tags = set(parse_tags(candidate.tags or ""))
        score += len(article_tags.intersection(candidate_tags))
        return score

    scored = [(cand, _related_score(cand)) for cand in related_candidates]
    scored.sort(key=lambda item: (item[1], item[0].published_at or datetime.min), reverse=True)
    related_articles = [item[0] for item in scored if item[1] > 0][:4]
    if len(related_articles) < 4:
        fallback = [cand for cand in related_candidates if cand not in related_articles]
        related_articles += fallback[: 4 - len(related_articles)]
    return render_template(
        "news/detail.html",
        title=article.title,
        article=article,
        tags=parse_tags(article.tags or ""),
        reading_time_minutes=reading_time_minutes,
        heading_links=heading_links,
        related_articles=related_articles,
        is_admin=is_admin,
        form_for_modal=EmptyForm(),
        article_schema=article_schema,
        author_schema=author_schema,
        breadcrumb_schema=breadcrumb_schema,
        global_org_schema=global_org.json_text if global_org else None,
        global_site_schema=global_site.json_text if global_site else None,
    )


@news_bp.route("/admin/news/new", methods=["GET", "POST"])
@login_required
@admin_required
def news_create():
    if request.method == "POST":
        form = ArticleForm(formdata=CombinedMultiDict([_normalise_published_formdata(request.form), request.files]))
    else:
        form = ArticleForm()
    if request.method == "GET" and not form.published_at.data:
        form.published_at.data = datetime.utcnow().date()
    if request.method == "POST" and not form.published_at.data:
        parsed = _parse_publish_date(request.form.get("published_at") if request.form else None)
        if parsed:
            form.published_at.data = parsed
    if form.validate_on_submit():
        slug = _ensure_unique_slug(form.slug.data or form.title.data)
        hero_image_url = None
        hero_file = form.hero_image_file.data
        if hero_file and hero_file.filename:
            if allowed_file(hero_file.filename):
                hero_image_url = _save_uploaded_image(hero_file)
            else:
                flash("Invalid hero image file type.", "warning")

        published_at = None
        if form.is_published.data:
            raw_publish = request.form.get("published_at") if request.form else None
            publish_date = form.published_at.data or _parse_publish_date(raw_publish) or datetime.utcnow().date()
            published_at = datetime.combine(publish_date, time.min)

        article = Article(
            title=form.title.data.strip(),
            slug=slug,
            excerpt=form.excerpt.data.strip() if form.excerpt.data else None,
            brand=form.brand.data.strip() if form.brand.data else None,
            tags=normalise_tags(form.tags.data),
            hero_image_url=hero_image_url,
            hero_image_alt=form.hero_image_alt.data.strip() if form.hero_image_alt.data else None,
            author_name=form.author_name.data.strip() if form.author_name.data else None,
            author_title=form.author_title.data.strip() if form.author_title.data else None,
            author_bio=form.author_bio.data.strip() if form.author_bio.data else None,
            author_image_alt=form.author_image_alt.data.strip() if form.author_image_alt.data else None,
            meta_title=form.meta_title.data.strip() if form.meta_title.data else None,
            meta_description=form.meta_description.data.strip() if form.meta_description.data else None,
            canonical_url=form.canonical_url.data.strip() if form.canonical_url.data else None,
            robots=form.robots.data or "index,follow",
            og_title=form.og_title.data.strip() if form.og_title.data else None,
            og_description=form.og_description.data.strip() if form.og_description.data else None,
            og_image_url=form.og_image_url.data.strip() if form.og_image_url.data else None,
            twitter_card=form.twitter_card.data or "summary_large_image",
            product_schema_json=None,
            faq_schema_json=None,
            video_schema_json=None,
            published_at=published_at,
            created_by_user_id=current_user.id,
        )
        author_image_url = None
        author_file = form.author_image_file.data
        if author_file and author_file.filename:
            if allowed_file(author_file.filename):
                author_image_url = _save_uploaded_image(author_file)
            else:
                flash("Invalid author image file type.", "warning")
        article.author_image_url = author_image_url
        article.product_schema_json = _clean_json_payload(form.product_schema_json.data)
        article.faq_schema_json = _clean_json_payload(form.faq_schema_json.data)
        article.video_schema_json = _clean_json_payload(form.video_schema_json.data)
        _update_global_schema_from_request()
        _apply_blocks_from_request(article)
        db.session.add(article)
        db.session.commit()
        if hero_image_url and not article.hero_image_alt:
            flash("Hero image is missing alt text. Add a short description for accessibility and SEO.", "warning")
        flash("Article created.", "success")
        return redirect(url_for("news.news_detail", slug=article.slug))
    if request.method == "POST" and form.errors:
        flash("Please check the highlighted fields and try again.", "danger")
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"{field}: {error}", "danger")

    slots = _collect_block_slots()
    global_org = SiteSchema.query.filter_by(schema_type="organization").first()
    global_site = SiteSchema.query.filter_by(schema_type="website").first()
    return render_template(
        "admin/news_form.html",
        title="New Article",
        form=form,
        slots=slots,
        block_types=BLOCK_TYPES,
        align_choices=ALIGN_CHOICES,
        submit_label="Create Article",
        global_org_schema=global_org.json_text if global_org else "",
        global_site_schema=global_site.json_text if global_site else "",
    )


@news_bp.route("/admin/news/<int:article_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def news_edit(article_id):
    article = db.session.get(Article, article_id)
    if not article:
        abort(404)

    if request.method == "POST":
        form = ArticleForm(formdata=CombinedMultiDict([_normalise_published_formdata(request.form), request.files]), obj=article)
    else:
        form = ArticleForm(obj=article)
    if request.method == "GET":
        form.slug.data = article.slug
        form.tags.data = article.tags or ""
        form.hero_image_url.data = article.hero_image_url or ""
        form.is_published.data = _article_is_published(article)
        if article.published_at:
            form.published_at.data = article.published_at.date()
        form.author_name.data = article.author_name or ""
        form.author_title.data = article.author_title or ""
        form.author_bio.data = article.author_bio or ""
        form.author_image_url.data = article.author_image_url or ""
        form.hero_image_alt.data = article.hero_image_alt or ""
        form.author_image_alt.data = article.author_image_alt or ""
        form.meta_title.data = article.meta_title or ""
        form.meta_description.data = article.meta_description or ""
        form.canonical_url.data = article.canonical_url or ""
        form.robots.data = article.robots or "index,follow"
        form.og_title.data = article.og_title or ""
        form.og_description.data = article.og_description or ""
        form.og_image_url.data = article.og_image_url or ""
        form.twitter_card.data = article.twitter_card or "summary_large_image"
        form.product_schema_json.data = article.product_schema_json or ""
        form.faq_schema_json.data = article.faq_schema_json or ""
        form.video_schema_json.data = article.video_schema_json or ""
    if request.method == "POST" and not form.published_at.data:
        parsed = _parse_publish_date(request.form.get("published_at") if request.form else None)
        if parsed:
            form.published_at.data = parsed

    if form.validate_on_submit():
        article.title = form.title.data.strip()
        article.slug = _ensure_unique_slug(form.slug.data or article.title, article_id=article.id)
        article.excerpt = form.excerpt.data.strip() if form.excerpt.data else None
        article.brand = form.brand.data.strip() if form.brand.data else None
        article.tags = normalise_tags(form.tags.data)
        article.author_name = form.author_name.data.strip() if form.author_name.data else None
        article.author_title = form.author_title.data.strip() if form.author_title.data else None
        article.author_bio = form.author_bio.data.strip() if form.author_bio.data else None
        article.hero_image_alt = form.hero_image_alt.data.strip() if form.hero_image_alt.data else None
        article.author_image_alt = form.author_image_alt.data.strip() if form.author_image_alt.data else None
        article.meta_title = form.meta_title.data.strip() if form.meta_title.data else None
        article.meta_description = form.meta_description.data.strip() if form.meta_description.data else None
        article.canonical_url = form.canonical_url.data.strip() if form.canonical_url.data else None
        article.robots = form.robots.data or "index,follow"
        article.og_title = form.og_title.data.strip() if form.og_title.data else None
        article.og_description = form.og_description.data.strip() if form.og_description.data else None
        article.og_image_url = form.og_image_url.data.strip() if form.og_image_url.data else None
        article.twitter_card = form.twitter_card.data or "summary_large_image"
        article.product_schema_json = _clean_json_payload(form.product_schema_json.data, previous=article.product_schema_json)
        article.faq_schema_json = _clean_json_payload(form.faq_schema_json.data, previous=article.faq_schema_json)
        article.video_schema_json = _clean_json_payload(form.video_schema_json.data, previous=article.video_schema_json)
        _update_global_schema_from_request()

        hero_file = form.hero_image_file.data
        if hero_file and hero_file.filename:
            if allowed_file(hero_file.filename):
                article.hero_image_url = _save_uploaded_image(hero_file)
            else:
                flash("Invalid hero image file type.", "warning")
        else:
            existing_hero = (request.form.get("hero_image_existing") or "").strip()
            if existing_hero:
                article.hero_image_url = existing_hero

        author_file = form.author_image_file.data
        if author_file and author_file.filename:
            if allowed_file(author_file.filename):
                article.author_image_url = _save_uploaded_image(author_file)
            else:
                flash("Invalid author image file type.", "warning")
        else:
            existing_author = (request.form.get("author_image_existing") or "").strip()
            if existing_author:
                article.author_image_url = existing_author

        if form.is_published.data:
            raw_publish = request.form.get("published_at") if request.form else None
            publish_date = (
                form.published_at.data
                or _parse_publish_date(raw_publish)
                or (article.published_at.date() if article.published_at else datetime.utcnow().date())
            )
            article.published_at = datetime.combine(publish_date, time.min)
        else:
            article.published_at = None

        _apply_blocks_from_request(article)
        db.session.commit()
        if article.hero_image_url and not article.hero_image_alt:
            flash("Hero image is missing alt text. Add a short description for accessibility and SEO.", "warning")
        flash("Article updated.", "success")
        return redirect(url_for("news.news_detail", slug=article.slug))
    if request.method == "POST" and form.errors:
        flash("Please check the highlighted fields and try again.", "danger")
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"{field}: {error}", "danger")

    slots = _collect_block_slots(article)
    global_org = SiteSchema.query.filter_by(schema_type="organization").first()
    global_site = SiteSchema.query.filter_by(schema_type="website").first()
    return render_template(
        "admin/news_form.html",
        title="Edit Article",
        form=form,
        slots=slots,
        block_types=BLOCK_TYPES,
        align_choices=ALIGN_CHOICES,
        submit_label="Save Changes",
        article=article,
        global_org_schema=global_org.json_text if global_org else "",
        global_site_schema=global_site.json_text if global_site else "",
    )


@news_bp.route("/admin/news/<int:article_id>/delete", methods=["POST"])
@login_required
@admin_required
def news_delete(article_id):
    form = EmptyForm()
    if not form.validate_on_submit():
        abort(400)
    article = db.session.get(Article, article_id)
    if not article:
        abort(404)
    db.session.delete(article)
    db.session.commit()
    flash("Article deleted.", "success")
    return redirect(url_for("news.news_index"))
