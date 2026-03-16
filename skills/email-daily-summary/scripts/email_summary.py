#!/usr/bin/env python3
"""
Email Daily Summary Skill - OpenClaw Email Summary Tool
Automatically logs into email accounts and generates daily email summaries
Supports Gmail, Outlook, QQ Mail, 163 Mail and other IMAP services
"""

import imaplib
import email
import json
import os
import sys
from datetime import datetime, timedelta
from email.header import decode_header
import argparse


def load_config():
    """Load email configuration from config file"""
    config_path = os.path.expanduser("~/.openclaw/email-daily-summary-config.json")
    if not os.path.exists(config_path):
        print(f"Configuration file not found: {config_path}")
        print("Please create the config file with your IMAP settings.")
        sys.exit(1)
    
    with open(config_path, 'r') as f:
        return json.load(f)


def decode_mime_words(s):
    """Decode MIME encoded words in email headers"""
    decoded_fragments = decode_header(s)
    fragments = []
    for fragment, encoding in decoded_fragments:
        if isinstance(fragment, bytes):
            if encoding:
                fragment = fragment.decode(encoding)
            else:
                fragment = fragment.decode('utf-8', errors='ignore')
        fragments.append(fragment)
    return ''.join(fragments)


def get_email_body(msg):
    """Extract email body from message"""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            
            if content_type == "text/plain" and "attachment" not in content_disposition:
                try:
                    body = part.get_payload(decode=True).decode('utf-8')
                except:
                    body = part.get_payload(decode=True).decode('latin-1')
                break
    else:
        try:
            body = msg.get_payload(decode=True).decode('utf-8')
        except:
            body = msg.get_payload(decode=True).decode('latin-1')
    
    return body[:500] + "..." if len(body) > 500 else body


def fetch_emails(config, days=1):
    """Fetch emails from the last N days"""
    # Connect to IMAP server
    if config['imap']['use_ssl']:
        mail = imaplib.IMAP4_SSL(config['imap']['host'], config['imap']['port'])
    else:
        mail = imaplib.IMAP4(config['imap']['host'], config['imap']['port'])
    
    mail.login(config['imap']['username'], config['imap']['password'])
    mail.select('inbox')
    
    # Calculate date range
    since_date = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
    
    # Search for emails since the specified date
    status, messages = mail.search(None, f'SINCE {since_date}')
    
    if status != 'OK':
        print("No emails found!")
        return []
    
    email_ids = messages[0].split()
    emails_data = []
    
    # Fetch details for each email
    for email_id in email_ids[-20:]:  # Limit to last 20 emails
        status, msg_data = mail.fetch(email_id, '(RFC822)')
        if status != 'OK':
            continue
            
        msg = email.message_from_bytes(msg_data[0][1])
        
        # Extract email details
        subject = decode_mime_words(msg.get("Subject", ""))
        sender = decode_mime_words(msg.get("From", ""))
        date_str = msg.get("Date", "")
        
        # Parse date
        try:
            email_date = email.utils.parsedate_to_datetime(date_str)
            formatted_date = email_date.strftime("%Y-%m-%d %H:%M")
        except:
            formatted_date = date_str
        
        # Get email body preview
        body_preview = get_email_body(msg)
        
        emails_data.append({
            'subject': subject,
            'sender': sender,
            'date': formatted_date,
            'body_preview': body_preview
        })
    
    mail.close()
    mail.logout()
    
    return emails_data


def generate_summary(emails_data):
    """Generate a daily email summary"""
    if not emails_data:
        return "📭 No new emails today."
    
    summary = f"📧 **Daily Email Summary**\n\n"
    summary += f"Total emails received: {len(emails_data)}\n\n"
    
    for i, email_data in enumerate(emails_data, 1):
        summary += f"**{i}. {email_data['subject']}**\n"
        summary += f"   From: {email_data['sender']}\n"
        summary += f"   Date: {email_data['date']}\n"
        summary += f"   Preview: {email_data['body_preview']}\n\n"
    
    return summary


def main():
    parser = argparse.ArgumentParser(description='Generate daily email summary via OpenClaw email-daily-summary skill')
    parser.add_argument('--days', type=int, default=1, help='Number of days to look back (default: 1)')
    parser.add_argument('--output', help='Output file path (optional, prints to stdout if not specified)')
    
    args = parser.parse_args()
    
    try:
        config = load_config()
        emails_data = fetch_emails(config, args.days)
        summary = generate_summary(emails_data)
        
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(summary)
            print(f"✅ Email summary saved to: {args.output}")
        else:
            print(summary)
            
    except Exception as e:
        print(f"❌ Failed to generate email summary: {str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    main()