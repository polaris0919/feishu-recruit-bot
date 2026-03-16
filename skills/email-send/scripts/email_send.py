#!/usr/bin/env python3
"""
Email Send Skill - OpenClaw Email Sending Tool
Supports Gmail, Outlook, QQ Mail, 163 Mail and other SMTP services
"""

import smtplib
import json
import os
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import argparse


def load_config():
    """Load email configuration from config file"""
    config_path = os.path.expanduser("~/.openclaw/email-send-config.json")
    if not os.path.exists(config_path):
        print(f"Configuration file not found: {config_path}")
        print("Please create the config file with your SMTP settings.")
        sys.exit(1)
    
    with open(config_path, 'r') as f:
        return json.load(f)


def send_email(to_emails, subject, body, html_body=None, attachments=None, config=None):
    """
    Send email using SMTP
    
    Args:
        to_emails: List of recipient email addresses
        subject: Email subject
        body: Plain text body
        html_body: HTML body (optional)
        attachments: List of file paths to attach (optional)
        config: SMTP configuration dict
    """
    if config is None:
        config = load_config()
    
    # Create message
    if html_body or attachments:
        msg = MIMEMultipart('alternative')
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        if html_body:
            msg.attach(MIMEText(html_body, 'html', 'utf-8'))
    else:
        msg = MIMEText(body, 'plain', 'utf-8')
    
    # Add attachments
    if attachments:
        for file_path in attachments:
            with open(file_path, "rb") as attachment:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header(
                'Content-Disposition',
                f'attachment; filename= {os.path.basename(file_path)}'
            )
            msg.attach(part)
    
    msg['Subject'] = subject
    msg['From'] = config['smtp']['from_email']
    msg['To'] = ', '.join(to_emails)
    
    # Send email
    try:
        if config['smtp']['use_tls']:
            server = smtplib.SMTP(config['smtp']['host'], config['smtp']['port'])
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(config['smtp']['host'], config['smtp']['port'])
        
        server.login(config['smtp']['username'], config['smtp']['password'])
        server.send_message(msg)
        server.quit()
        print(f"✅ Email sent successfully to: {', '.join(to_emails)}")
        return True
    except Exception as e:
        print(f"❌ Failed to send email: {str(e)}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Send email via OpenClaw email-send skill')
    parser.add_argument('--to', required=True, help='Recipient email address(es), comma-separated')
    parser.add_argument('--subject', required=True, help='Email subject')
    parser.add_argument('--body', required=True, help='Email body (plain text)')
    parser.add_argument('--html', help='HTML body (optional)')
    parser.add_argument('--attachment', action='append', help='File path to attach (can be used multiple times)')
    
    args = parser.parse_args()
    
    to_emails = [email.strip() for email in args.to.split(',')]
    attachments = args.attachment if args.attachment else []
    
    success = send_email(
        to_emails=to_emails,
        subject=args.subject,
        body=args.body,
        html_body=args.html,
        attachments=attachments
    )
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()