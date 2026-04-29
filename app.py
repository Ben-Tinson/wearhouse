# app.py
import os
import click
from flask import Flask, current_app, url_for
from werkzeug.routing import BaseConverter
from extensions import db, migrate, login_manager, mail, csrf
from models import User
from utils.money import format_money, display_money
from services.article_render import render_markdown
from config import Config, TestConfig

# Import your Blueprints
from routes.auth_routes import auth_bp
from routes.main_routes import main_bp
from routes.news_routes import news_bp
from routes.sneakers_routes import sneakers_bp
from utils.slugs import build_my_sneaker_slug, build_product_key, build_product_slug
from services.heat_service import heat_label_for_score, heat_tooltip
from services.steps_seed_service import seed_fake_steps, seed_fake_wear, verify_steps_attribution


class NoDashConverter(BaseConverter):
    regex = r"[^-]+"


# Define App Configuration
UPLOAD_FOLDER = 'uploads'
basedir = os.path.abspath(os.path.dirname(__file__))

def create_app(config_class=Config): # Existing default
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.url_map.converters["nodash"] = NoDashConverter

    # --- ADD THIS DEBUGGING BLOCK ---
    print("\n--- Flask App Configuration Check ---")
    print(f"SECRET_KEY loaded: {'Yes' if app.config.get('SECRET_KEY') else 'No'}")
    print(f"DATABASE_URI loaded: {app.config.get('SQLALCHEMY_DATABASE_URI')}")
    print(f"RAPIDAPI_KEY loaded: {app.config.get('RAPIDAPI_KEY')}")
    print(f"RAPIDAPI_HOST loaded: {app.config.get('RAPIDAPI_HOST')}")
    print("---------------------------------\n")
    # --- END DEBUGGING BLOCK ---

    # Initialize extensions with the app
    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'info'
    mail.init_app(app)
    csrf.init_app(app)

    # User loader for Flask-Login
    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))
    
    with app.app_context():
        # Register Blueprints
        app.register_blueprint(auth_bp)
        app.register_blueprint(main_bp)
        app.register_blueprint(news_bp)
        app.register_blueprint(sneakers_bp)

        app.jinja_env.globals["build_my_sneaker_slug"] = build_my_sneaker_slug
        app.jinja_env.globals["build_product_key"] = build_product_key
        app.jinja_env.globals["build_product_slug"] = build_product_slug
        app.jinja_env.globals["heat_label_for_score"] = heat_label_for_score
        app.jinja_env.globals["heat_tooltip"] = heat_tooltip

    def money_display_filter(amount, currency, preferred_currency=None):
        return display_money(db.session, amount, currency, preferred_currency)

    def release_image_filter(image_url):
        if not image_url:
            return url_for("static", filename="images/placeholder.png")
        lowered = image_url.lower()
        if "product-placeholder-default" in lowered or "stockx-assets.imgix.net/media/product-placeholder" in lowered:
            return url_for("static", filename="images/placeholder.png")
        return image_url

    app.jinja_env.filters["money"] = format_money
    app.jinja_env.filters["money_display"] = money_display_filter
    app.jinja_env.filters["release_image"] = release_image_filter
    app.jinja_env.globals["render_markdown"] = render_markdown

    @app.cli.command("steps:seed-fake")
    @click.option("--days", default=14, type=int, show_default=True)
    @click.option("--steps-min", default=6000, type=int, show_default=True)
    @click.option("--steps-max", default=12000, type=int, show_default=True)
    @click.option("--source", default="apple_health", show_default=True)
    @click.option("--granularity", default="day", show_default=True)
    @click.option("--timezone", default="Europe/London", show_default=True)
    @click.option("--user-id", type=int)
    @click.option("--user-email")
    @click.option("--seed", default=None)
    def seed_fake_steps_command(days, steps_min, steps_max, source, granularity, timezone, user_id, user_email, seed):
        """Seed deterministic fake step buckets for a user and recompute attribution."""
        target_user = None
        if user_id:
            target_user = db.session.get(User, user_id)
        elif user_email:
            target_user = User.query.filter_by(email=user_email).first()

        if not target_user:
            click.echo("User not found. Provide --user-id or --user-email.")
            raise SystemExit(1)

        stats = seed_fake_steps(
            user_id=target_user.id,
            days=days,
            steps_min=steps_min,
            steps_max=steps_max,
            source=source,
            granularity=granularity,
            timezone_name=timezone,
            seed=seed,
        )
        click.echo(
            "Seeded fake steps for user_id=%s (%s). Range %s -> %s. "
            "buckets_upserted=%s buckets_updated=%s attributions_written=%s"
            % (
                target_user.id,
                target_user.email,
                stats["start_date"],
                stats["end_date"],
                stats["buckets_upserted"],
                stats["buckets_updated"],
                stats["attributions_written"],
            )
        )

    @app.cli.command("steps:verify")
    @click.option("--days", default=14, type=int, show_default=True)
    @click.option("--granularity", default="day", show_default=True)
    @click.option("--user-id", type=int)
    @click.option("--user-email")
    def verify_steps_command(days, granularity, user_id, user_email):
        """Verify step buckets and attribution totals for a user."""
        target_user = None
        if user_id:
            target_user = db.session.get(User, user_id)
        elif user_email:
            target_user = User.query.filter_by(email=user_email).first()

        if not target_user:
            click.echo("User not found. Provide --user-id or --user-email.")
            raise SystemExit(1)

        stats = verify_steps_attribution(
            user_id=target_user.id,
            days=days,
            granularity=granularity,
        )
        click.echo(
            "Verify steps for user_id=%s (%s). Range %s -> %s."
            % (target_user.id, target_user.email, stats["start_date"], stats["end_date"])
        )
        click.echo("Bucket totals:")
        for row in stats["bucket_lines"]:
            click.echo("  %s: %s steps" % (row["date"], row["steps"]))
        click.echo("Attribution totals:")
        for row in stats["attribution_lines"]:
            click.echo(
                "  sneaker_id=%s name=%s steps=%s"
                % (row["sneaker_id"], row["sneaker_name"], row["steps"])
            )
        click.echo(
            "Totals check: bucket=%s attributed=%s"
            % (stats["total_bucket_steps"], stats["total_attributed_steps"])
        )
        if stats["missing_wear_days"]:
            click.echo("No wear data for: %s" % ", ".join(stats["missing_wear_days"]))

    @app.cli.command("wear:seed-fake")
    @click.option("--days", default=14, type=int, show_default=True)
    @click.option("--sneaker-ids", required=True)
    @click.option("--timezone", default="Europe/London", show_default=True)
    @click.option("--user-id", type=int)
    @click.option("--user-email")
    def seed_fake_wear_command(days, sneaker_ids, timezone, user_id, user_email):
        """Seed fake wear dates for a user and sneaker ids."""
        target_user = None
        if user_id:
            target_user = db.session.get(User, user_id)
        elif user_email:
            target_user = User.query.filter_by(email=user_email).first()

        if not target_user:
            click.echo("User not found. Provide --user-id or --user-email.")
            raise SystemExit(1)

        sneaker_id_list = [int(value) for value in sneaker_ids.split(",") if value.strip().isdigit()]
        if not sneaker_id_list:
            click.echo("No valid sneaker ids provided.")
            raise SystemExit(1)

        stats = seed_fake_wear(
            user_id=target_user.id,
            days=days,
            sneaker_ids=sneaker_id_list,
            timezone_name=timezone,
        )
        click.echo(
            "Seeded wear dates for user_id=%s. Range %s -> %s. wears_created=%s"
            % (
                target_user.id,
                stats["start_date"],
                stats["end_date"],
                stats["wears_created"],
            )
        )

    return app

# Main entry point for running the app
if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)
