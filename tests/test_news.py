from datetime import datetime

from extensions import db
from models import Article


def test_news_feed_only_published(test_client, test_app):
    with test_app.app_context():
        published = Article(
            title="Published Story",
            slug="published-story",
            excerpt="Published excerpt",
            published_at=datetime.utcnow(),
        )
        draft = Article(
            title="Draft Story",
            slug="draft-story",
            excerpt="Draft excerpt",
            published_at=None,
        )
        db.session.add_all([published, draft])
        db.session.commit()

    response = test_client.get("/news")
    assert response.status_code == 200
    assert b"Published Story" in response.data
    assert b"Draft Story" not in response.data


def test_news_detail_404_for_draft(test_client, test_app):
    with test_app.app_context():
        draft = Article(
            title="Hidden Draft",
            slug="hidden-draft",
            excerpt="Draft excerpt",
            published_at=None,
        )
        db.session.add(draft)
        db.session.commit()

    response = test_client.get("/news/hidden-draft")
    assert response.status_code == 404


def test_admin_create_article_slug_unique(test_client, auth, admin_user, test_app):
    with test_app.app_context():
        existing = Article(
            title="Test Article",
            slug="test-article",
            excerpt="Existing excerpt",
            published_at=datetime.utcnow(),
        )
        db.session.add(existing)
        db.session.commit()

    auth.login(username=admin_user.username, password="password123")
    response = test_client.post(
        "/admin/news/new",
        data={
            "title": "Test Article",
            "slug": "test-article",
            "brand": "Nike",
            "excerpt": "New excerpt",
            "tags": "Jordan, Retro",
            "published_at": "2026-01-01 10:00",
            "is_published": "y",
            "hero_image_option": "url",
            "hero_image_url": "https://example.com/hero.jpg",
            "block_type_1": "heading",
            "block_heading_1": "Intro",
            "block_type_2": "body",
            "block_body_2": "Body text",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with test_app.app_context():
        created = Article.query.filter_by(excerpt="New excerpt").first()
        assert created is not None
        assert created.slug != "test-article"


def test_news_filter_by_brand(test_client, test_app):
    with test_app.app_context():
        nike = Article(
            title="Nike Drop",
            slug="nike-drop",
            brand="Nike",
            published_at=datetime.utcnow(),
        )
        adidas = Article(
            title="Adidas Drop",
            slug="adidas-drop",
            brand="Adidas",
            published_at=datetime.utcnow(),
        )
        db.session.add_all([nike, adidas])
        db.session.commit()

    response = test_client.get("/news?brand=Nike")
    assert response.status_code == 200
    assert b"Nike Drop" in response.data
    assert b"Adidas Drop" not in response.data
