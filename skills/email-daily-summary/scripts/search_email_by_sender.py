#!/usr/bin/env python3
"""
Search and display full email content from specific sender
"""

import imaplib
import email
import json
import os
import sys
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
    if not s:
        return ""
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


def get_full_email_body(msg):
    """Extract full email body from message"""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            
            # Skip attachments
            if "attachment" in content_disposition:
                continue
                
            if content_type == "text/plain":
                try:
                    body = part.get_payload(decode=True).decode('utf-8')
                except:
                    try:
                        body = part.get_payload(decode=True).decode('latin-1')
                    except:
                        body = str(part.get_payload(decode=True))
                break
            elif content_type == "text/html" and not body:
                try:
                    html_body = part.get_payload(decode=True).decode('utf-8')
                    # Store HTML body as fallback if no text/plain found
                    if not body:
                        body = html_body
                except:
                    try:
                        html_body = part.get_payload(decode=True).decode('latin-1')
                        if not body:
                            body = html_body
                    except:
                        if not body:
                            body = str(part.get_payload(decode=True))
    else:
        try:
            body = msg.get_payload(decode=True).decode('utf-8')
        except:
            try:
                body = msg.get_payload(decode=True).decode('latin-1')
            except:
                body = str(msg.get_payload(decode=True))
    
    return body


def search_emails_by_sender(config, sender_keyword, limit=10):
    """Search emails from sender containing keyword"""
    # Connect to IMAP server
    if config['imap']['use_ssl']:
        mail = imaplib.IMAP4_SSL(config['imap']['host'], config['imap']['port'])
    else:
        mail = imaplib.IMAP4(config['imap']['host'], config['imap']['port'])
    
    mail.login(config['imap']['username'], config['imap']['password'])
    mail.select('inbox')
    
    # Search for all emails
    status, messages = mail.search(None, 'ALL')
    
    if status != 'OK':
        print("No emails found!")
        return []
    
    email_ids = messages[0].split()
    matching_emails = []
    
    # Search through emails (start from newest)
    for email_id in reversed(email_ids[-limit*5:]):  # Check more emails to find matches
        status, msg_data = mail.fetch(email_id, '(RFC822)')
        if status != 'OK':
            continue
            
        msg = email.message_from_bytes(msg_data[0][1])
        
        # Extract email details
        subject = decode_mime_words(msg.get("Subject", ""))
        sender = decode_mime_words(msg.get("From", ""))
        date_str = msg.get("Date", "")
        
        # Check if sender contains the keyword (case insensitive)
        if sender_keyword.lower() in sender.lower():
            # Get full email body
            body = get_full_email_body(msg)
            
            # Parse date
            try:
                email_date = email.utils.parsedate_to_datetime(date_str)
                formatted_date = email_date.strftime("%Y-%m-%d %H:%M:%S")
            except:
                formatted_date = date_str
            
            matching_emails.append({
                'subject': subject,
                'sender': sender,
                'date': formatted_date,
                'body': body,
                'email_id': email_id
            })
            
            # Return first match (most recent)
            break
    
    mail.close()
    mail.logout()
    
    return matching_emails


def main():
    parser = argparse.ArgumentParser(description='Search and display full email from specific sender')
    parser.add_argument('--sender', required=True, help='Sender name or email to search for')
    parser.add_argument('--limit', type=int, default=10, help='Number of recent emails to check (default: 10)')
    
    args = parser.parse_args()
    
    try:
        config = load_config()
        matching_emails = search_emails_by_sender(config, args.sender, args.limit)
        
        if not matching_emails:
            print(f"❌ No emails found from sender containing '{args.sender}'")
            return
        
        email_data = matching_emails[0]
        print("=" * 60)
        print("📧 FULL EMAIL CONTENT")
        print("=" * 60)
        print(f"Subject: {email_data['subject']}")
        print(f"From: {email_data['sender']}")
        print(f"Date: {email_data['date']}")
        print("-" * 60)
        print(email_data['body'])
        print("=" * 60)
        print("✅ Email retrieved successfully!")
            
    except Exception as e:
        print(f"❌ Failed to retrieve email: {str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    main()