# Email Daily Summary

Automatically logs into email accounts (Gmail, Outlook, QQ Mail, etc.) and generates daily email summaries. Use when the user wants to get a summary of their emails, check important messages, or create daily email digests.

## Features
- Supports multiple email providers (Gmail, Outlook, QQ Mail, 163 Mail, etc.)
- IMAP-based email reading
- Daily email summary generation
- Important message detection
- Unread email tracking
- Customizable summary format

## Usage
```bash
# Generate daily summary for configured email account
python3 scripts/email_daily_summary.py --config ~/.openclaw/email-config.json

# Generate summary with custom date range
python3 scripts/email_daily_summary.py --config ~/.openclaw/email-config.json --days 7

# Output summary to file
python3 scripts/email_daily_summary.py --config ~/.openclaw/email-config.json --output summary.md
```

## Configuration
Create a config file with your email IMAP settings:
```json
{
  "imap": {
    "host": "imap.qq.com",
    "port": 993,
    "username": "your-email@qq.com",
    "password": "your-app-password",
    "use_ssl": true
  }
}
```