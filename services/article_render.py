from markupsafe import Markup
import bleach
import markdown


ALLOWED_TAGS = [
    "p",
    "br",
    "strong",
    "em",
    "a",
    "ul",
    "ol",
    "li",
    "blockquote",
    "h2",
    "h3",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel", "target"],
}


def render_markdown(text: str) -> Markup:
    if not text:
        return Markup("")
    normalized = _normalize_bullets(text)
    html = markdown.markdown(normalized, extensions=["extra", "sane_lists", "nl2br"])
    cleaned = bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)
    cleaned = bleach.linkify(
        cleaned,
        callbacks=[bleach.callbacks.nofollow, bleach.callbacks.target_blank],
        skip_tags=["a"],
        parse_email=False,
    )
    return Markup(cleaned)


def _normalize_bullets(text: str) -> str:
    lines = text.splitlines()
    normalized_lines = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(("• ", "· ", "– ", "— ")):
            prefix_len = len(line) - len(stripped)
            normalized_lines.append(" " * prefix_len + "- " + stripped[2:])
        else:
            normalized_lines.append(line)
    return "\n".join(normalized_lines)
