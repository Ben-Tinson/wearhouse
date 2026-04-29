# email_utils.py
import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from flask import current_app

def send_email(to_email, subject, html_content):
    """Sends an email using SendGrid."""
    sender = current_app.config.get('MAIL_DEFAULT_SENDER')
    api_key = current_app.config.get('SENDGRID_API_KEY')
    if not sender:
        current_app.logger.error("Email send skipped: MAIL_DEFAULT_SENDER is not configured.")
        return False
    if not api_key:
        current_app.logger.error("Email send skipped: SENDGRID_API_KEY is not configured.")
        return False

    message = Mail(
        from_email=sender,
        to_emails=to_email,
        subject=subject,
        html_content=html_content
    )
    try:
        sendgrid_client = SendGridAPIClient(api_key)
        response = sendgrid_client.send(message)
        current_app.logger.info("Email sent to %s with status code %s", to_email, response.status_code)
        return True
    except Exception as e:
        current_app.logger.error("Error sending email to %s: %s", to_email, e)
        return False
