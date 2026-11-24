# Conference

Conference is a local research feed for papers, preprints, and datasets that match the work you follow. It runs with Python 3.9+ and the standard library.

## Start

```sh
cd conference
python3 conference.py
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). Use `--port 8766` if another local service uses port 8765. Stop the server with Ctrl+C.

1. Open **Research profile** and enter an ORCID iD or profile URL. Conference reads public works, suggests topics from their titles, and lets you review each suggestion. You can also enter topics manually. This is public-record import. Identity verification and sign-in would require an ORCID OAuth integration.
2. Save the topics you want to follow. Open **Research feed** and click **Refresh feed**. Each card links to the original record and explains what matched. When a topic appears in an imported publication, Conference shows the title of that publication as context.
3. Open **Email digest**, enter your address, and save a daily or weekly frequency. **Preview next digest** shows the most recent pending matches. With SMTP configured, the server checks delivery every five minutes while running. The first eligible digest can send on the next check. Later digests are spaced by one or seven days.

Conference stores profiles, settings, matches, and delivery history in local SQLite. To choose the file, use `python3 conference.py --db /path/to/conference.sqlite3`. The original prototype remains runnable via `python3 biomastermind.py serve --port 8767`.

## Public sources

- Europe PMC for papers and preprints. PubMed serves as the fallback, with different preprint coverage.
- Zenodo for datasets. A revised version of the same Zenodo record updates its existing feed entry.
- ORCID for public names and works. If the ORCID endpoint requires authentication, supply an ORCID `/read-public` access token via `CONFERENCE_ORCID_TOKEN` before starting. The token stays on the server.

Each scan checks up to 45 publications and 25 datasets. Conference looks for the followed phrases in returned titles or descriptions, ranks title matches higher, and skips works already present in the imported ORCID list when a DOI or normalized title matches. Scans retain previous relevant results for the active set of topics. Source coverage in the dashboard shows how many records were retrieved and flags source failures. Public search terms are sent to the repositories.

## Configure email delivery

Set these environment variables where you run Conference. Use a provider-issued SMTP credential and a sender address accepted by that provider. Authenticated SMTP normally requires a password or an app-specific password. Enter secrets through your service manager or shell's secure configuration.

| Variable | Purpose |
| --- | --- |
| `CONFERENCE_SMTP_HOST` | Your SMTP server |
| `CONFERENCE_SMTP_FROM` | The configured sender address |
| `CONFERENCE_SMTP_USERNAME`, `CONFERENCE_SMTP_PASSWORD` | SMTP credentials when required |
| `CONFERENCE_SMTP_TLS` | `starttls` (default) or `ssl` |
| `CONFERENCE_SMTP_PORT` | Defaults to 587 for STARTTLS or 465 for SSL |

Restart the server after setting these variables. The digest includes up to 20 matches per delivery. Conference records accepted deliveries per recipient, skips empty digests, and retries items after a delivery failure. SMTP acceptance can be verified by the server. Inbox placement depends on the receiving provider.

`python3 conference.py refresh` refreshes the stored feed once. `python3 conference.py digest` prints the next digest without sending it. Keep the main server running for automatic refreshes and email checks.
