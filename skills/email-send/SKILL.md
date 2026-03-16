# email-send

Send emails via SMTP with support for major email providers.

## Description

This skill provides email sending capabilities through SMTP protocol. It supports Gmail, Outlook, QQ Mail, 163 Mail, and other SMTP-compatible email services.

## Commands

### Basic Usage
```bash
uv run {baseDir}/scripts/email_send.py --to "recipient@example.com" --subject "Subject" --body "Message body"
```

### HTML Email
```bash
uv run {baseDir}/scripts/email_send.py --to "recipient@example.com" --subject "Subject" --html "<h1>HTML Content</h1>"
```

### With Attachment
```bash
uv run {baseDir}/scripts/email_send.py --to "recipient@example.com" --subject "Subject" --body "See attachment" --attachment "/path/to/file.pdf"
```

### Configuration
Create `~/.openclaw/email-config.json` with your SMTP settings:

```json
{
  "smtp_server": "smtp.gmail.com",
  "smtp_port": 587,
  "username": "your-email@gmail.com",
  "password": "your-app-password",
  "use_tls": true
}
```

## Supported Providers

- **Gmail**: smtp.gmail.com:587 (requires app password)
- **Outlook**: smtp-mail.outlook.com:587
- **QQ Mail**: smtp.qq.com:587 (requires authorization code)
- **163 Mail**: smtp.163.com:25 or 465
- **Custom SMTP**: Any SMTP server with proper credentials

## Security

- Credentials are stored in `~/.openclaw/email-config.json`
- Password should be an app-specific password for Gmail
- TLS/SSL encryption is used for secure transmission

## Requirements

- Python 3.6+
- `uv` runtime manager (included with OpenClaw)