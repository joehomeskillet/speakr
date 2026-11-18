# Email Setup

This guide explains how to configure outgoing email in Speakr for processing notifications.

## Overview

Email features in Speakr are completely opt-in. When configured, they provide:

- **Processing Notifications**: Let each user opt in to a message when one of their recordings finishes transcribing, or fails

## Prerequisites

- SMTP server credentials (Gmail, SendGrid, Mailgun, Amazon SES, or any SMTP provider)
- Speakr instance accessible via the URL you configure (for email links to work)

## Configuration

### Required Environment Variables

Set these variables in your `.env` file (see `config/env.email.example` for a complete template):

```bash
# SMTP Configuration
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_USE_TLS=true
SMTP_FROM_ADDRESS=noreply@yourdomain.com
SMTP_FROM_NAME=Speakr
```

Restart Speakr after updating environment variables.

### Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `SMTP_HOST` | (none) | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP server port |
| `SMTP_USERNAME` | (none) | SMTP authentication username |
| `SMTP_PASSWORD` | (none) | SMTP authentication password |
| `SMTP_USE_TLS` | `true` | Use STARTTLS encryption (port 587) |
| `SMTP_USE_SSL` | `false` | Use SSL encryption (port 465) |
| `SMTP_FROM_ADDRESS` | `noreply@yourdomain.com` | Email address shown in "From" field |
| `SMTP_FROM_NAME` | `Speakr` | Display name shown alongside from address |
| `APP_BASE_URL` | (none) | External URL of this instance, used for all links in email; recommended whenever SMTP is configured |

### Processing Notifications

Once SMTP works, each user can turn on "Email me when a transcription finishes"
under Account, Preferences. It is off by default, so upgrading an existing
instance does not start mailing anyone. The message is sent when a transcription
completes and when one fails permanently, including a transcription that
returned no text and one stopped by the user's monthly transcription budget. Summaries do not send mail, since a
summary is quick and follows a transcript the user has already been told about.

Two things suppress the mail regardless of the user's preference. SMTP has to be
configured, and the address has to be one Speakr can deliver to: an SSO account
whose provider withheld an address is given a synthetic `@placeholder.local` one,
and mailing that on every finished recording would bounce repeatedly and put the
sending domain's reputation at risk. The toggle is hidden for those accounts
rather than shown and quietly ignored.

Set `APP_BASE_URL` to the address your users reach Speakr on, with no trailing
path:

```bash
APP_BASE_URL=https://speakr.example.com
```

Links in notification mail are built from this address. Notification mail is
sent from a background worker, which has no request to look at, so without this
variable a notification is sent without a link to the recording.

Note that each recording sends its own message, so a bulk upload of fifty files
produces fifty emails. The setting is most useful for long recordings the user
does not want to wait on.

## Provider-Specific Setup

### Gmail

```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
```

**Important:** Use an [App Password](https://support.google.com/accounts/answer/185833), not your regular Gmail password. App Passwords are required when 2-factor authentication is enabled (recommended).

### SendGrid

```bash
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=apikey
SMTP_PASSWORD=your-sendgrid-api-key
```

### Mailgun

```bash
SMTP_HOST=smtp.mailgun.org
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=postmaster@your-domain.mailgun.org
SMTP_PASSWORD=your-mailgun-password
```

### Amazon SES

```bash
SMTP_HOST=email-smtp.us-east-1.amazonaws.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=your-ses-smtp-username
SMTP_PASSWORD=your-ses-smtp-password
```

### Microsoft 365 / Outlook

```bash
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=your-email@yourdomain.com
SMTP_PASSWORD=your-password
```

### SSL vs TLS

- **Port 587 with TLS** (recommended): Set `SMTP_USE_TLS=true`, `SMTP_USE_SSL=false`
- **Port 465 with SSL**: Set `SMTP_USE_TLS=false`, `SMTP_USE_SSL=true`
- **Port 25 (unencrypted)**: Not recommended for security reasons

## Security Considerations

1. **Use secure SMTP connections**: Always enable TLS or SSL
2. **Use app-specific passwords**: When available (Gmail, etc.)
3. **Consider dedicated email services**: SendGrid, Mailgun, and SES offer better deliverability than personal email accounts

## Troubleshooting

### Emails not sending

1. Check Docker logs: `docker compose logs -f app`
2. Verify SMTP credentials are correct
3. Ensure SMTP port is not blocked by firewall
4. Try sending a test email using the same credentials from another tool

### Emails going to spam

1. Use a proper `SMTP_FROM_ADDRESS` that matches your domain
2. Configure SPF and DKIM records for your domain
3. Consider using a dedicated email service with good reputation

### "SMTP not configured" errors

Ensure all required SMTP variables are set:

- `SMTP_HOST`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`

---

Next: [SSO Setup](sso-setup.md) →
