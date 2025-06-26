# email_utils.py
import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from flask import current_app

def send_email(to_email, subject, html_content):
    """Sends an email using SendGrid."""
    message = Mail(
        from_email=current_app.config['MAIL_DEFAULT_SENDER'],
        to_emails=to_email,
        subject=subject,
        html_content=html_content
    )
    try:
        sendgrid_client = SendGridAPIClient(current_app.config['SENDGRID_API_KEY'])
        response = sendgrid_client.send(message)
        print(f"Email sent to {to_email} with status code {response.status_code}")
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False