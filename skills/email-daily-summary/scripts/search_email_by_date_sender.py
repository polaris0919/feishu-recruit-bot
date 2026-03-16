#!/usr/bin/env python3
"""
Search and display full email content from specific sender within date range
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


def search_emails_by_date_and_sender(config, sender_keyword, target_date_str):
    """Search emails from sender containing keyword on specific date"""
    # Connect to IMAP server
    if config['imap']['use_ssl']:
        mail = imaplib.IMAP4_SSL(config['imap']['host'], config['imap']['port'])
    else:
        mail = imaplib.IMAP4(config['imap']['host'], config['imap']['port'])
    
    mail.login(config['imap']['username'], config['imap']['password'])
    mail.select('inbox')
    
    # Convert target date to IMAP format (DD-MMM-YYYY)
    target_date = datetime.strptime(target_date_str, "%Y-%m-%d")
    imap_date = target_date.strftime("%d-%b-%Y")
    
    # Search for emails on the specific date
    status, messages = mail.search(None, f'ON "{imap_date}"')
    
    if status != 'OK' or not messages[0]:
        # If no emails found on exact date, try date range
        print(f"No emails found on exact date {target_date_str}, searching broader range...")
        # Search all emails and filter by date
        status, messages = mail.search(None, 'ALL')
    
    if status != 'OK':
        print("No emails found!")
        return []
    
    email_ids = messages[0].split()
    matching_emails = []
    
    # Search through all emails to find matches
    for email_id in reversed(email_ids):  # Start from newest
        status, msg_data = mail.fetch(email_id, '(RFC822)')
        if status != 'OK':
            continue
            
        msg = email.message_from_bytes(msg_data[0][1])
        
        # Extract email details
        subject = decode_mime_words(msg.get("Subject", ""))
        sender = decode_mime_words(msg.get("From", ""))
        date_str = msg.get("Date", "")
        
        # Parse email date
        try:
            email_date = email.utils.parsedate_to_datetime(date_str)
            email_date_str = email_date.strftime("%Y-%m-%d")
        except:
            email_date_str = ""
        
        # Check if sender contains the keyword AND date matches (or is close)
        sender_match = sender_keyword.lower() in sender.lower() or sender_keyword.lower() in sender.replace(" ", "").lower()
        date_match = target_date_str in email_date_str if email_date_str else False
        
        if sender_match or (sender_match and date_match):
            # Get full email body
            body = get_full_email_body(msg)
            
            # Format date
            try:
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
            break  # Return first match
    
    mail.close()
    mail.logout()
    
    return matching_emails


def main():
    parser = argparse.ArgumentParser(description='Search and display full email from specific sender on specific date')
    parser.add_argument('--sender', required=True, help='Sender email or name to search for')
    parser.add_argument('--date', required=True, help='Target date in YYYY-MM-DD format')
    
    args = parser.parse_args()
    
    try:
        config = load_config()
        matching_emails = search_emails_by_date_and_sender(config, args.sender, args.date)
        
        if not matching_emails:
            print(f"❌ No emails found from sender '{args.sender}' around date '{args.date}'")
            return
        
        email_data = matching_emails[0]
        print(email_data['body'])
            
    except Exception as e:
        print(f"❌ Failed to retrieve email: {str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    main()