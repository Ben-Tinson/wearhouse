from services.article_render import render_markdown


def test_render_markdown_sanitises_and_formats():
    output = render_markdown("**bold** *italic* [x](https://example.com) <script>alert(1)</script>")
    html = str(output)
    assert "<strong>" in html
    assert "<em>" in html
    assert "href=\"https://example.com\"" in html
    assert "script" not in html


def test_admin_news_form_includes_markdown_toolbar(test_client, auth, admin_user):
    auth.login(username=admin_user.username, password="password123")
    response = test_client.get("/admin/news/new")
    assert response.status_code == 200
    assert b"markdown_toolbar.js" in response.data
