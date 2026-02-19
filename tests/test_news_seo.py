from datetime import datetime

from models import Article, ArticleBlock, SiteSchema


def test_news_detail_seo_meta_and_headings(test_client, test_app):
    with test_app.app_context():
        test_app.extensions["sqlalchemy"].session.add(
            SiteSchema(schema_type="organization", json_text="{\"@context\":\"https://schema.org\",\"@type\":\"Organization\"}")
        )
        test_app.extensions["sqlalchemy"].session.add(
            SiteSchema(schema_type="website", json_text="{\"@context\":\"https://schema.org\",\"@type\":\"WebSite\"}")
        )
        article = Article(
            title="SEO Title",
            slug="seo-title",
            excerpt="Short excerpt",
            meta_title="Custom Meta Title",
            meta_description="Custom meta description here.",
            canonical_url="https://example.com/news/seo-title",
            robots="noindex,follow",
            og_title="OG Title",
            og_description="OG Description",
            twitter_card="summary",
            hero_image_url="hero.jpg",
            hero_image_alt="Hero alt text",
            product_schema_json="{\"@type\":\"Product\"}",
            faq_schema_json="{\"@type\":\"FAQPage\"}",
            published_at=datetime.utcnow(),
        )
        article.blocks.append(
            ArticleBlock(
                position=1,
                block_type="heading",
                heading_text="Section Heading",
                heading_level="h3",
            )
        )
        article.blocks.append(
            ArticleBlock(
                position=2,
                block_type="image",
                image_url="block.jpg",
                image_alt="Block alt",
            )
        )
        test_app.extensions["sqlalchemy"].session.add(article)
        test_app.extensions["sqlalchemy"].session.commit()

    response = test_client.get("/news/seo-title")
    assert response.status_code == 200
    data = response.data
    assert b"<meta name=\"description\" content=\"Custom meta description here." in data
    assert b"<link rel=\"canonical\" href=\"https://example.com/news/seo-title\"" in data
    assert b"noindex,follow" in data
    assert b"twitter:card" in data
    assert b"<h3 class=\"mt-4\"" in data
    assert b"Section Heading</h3>" in data
    assert b"alt=\"Hero alt text\"" in data
    assert b"alt=\"Block alt\"" in data
    assert b"application/ld+json" in data
    assert b"Organization" in data
    assert b"WebSite" in data
    assert b"Product" in data
    assert b"FAQPage" in data
