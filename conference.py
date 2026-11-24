"""Conference, a local research watch and email digest."""

import argparse
import json
import logging
import os
import re
import smtplib
import sqlite3
import ssl
import threading
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from xml.etree.ElementTree import ParseError

from biomastermind import clean, dates, papers_feed, zenodo

ROOT = Path(__file__).resolve().parent
DAY = 86400
DEFAULT = {"topics": [], "days": 30, "auto_refresh": True, "email": "", "frequency": "off"}
ORCID_PATTERN = r"(?:https://orcid\.org/)?([0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9Xx])/?"
EMAIL_PATTERN = r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+"
STOP = set("a an and by for from in into of on or the to using via with across among between through toward towards under based novel new study studies evidence analysis approach characterization role effects data results model using human mice associated reveals formation first framework".split())
SOURCE_ERRORS = (OSError, ValueError, KeyError, TypeError, ParseError)


def normalized_words(value):
    folded = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(re.findall(r"[^\W_]+", "".join(c for c in folded if not unicodedata.combining(c))))


def normalized_doi(value):
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip().lower())


def orcid_id(value):
    match = re.fullmatch(ORCID_PATTERN, value.strip(), re.IGNORECASE)
    if not match:
        raise ValueError("Enter an ORCID iD or an orcid.org profile URL.")
    identifier = match[1].upper()
    digits = identifier.replace("-", "")
    total = 0
    for digit in digits[:-1]:
        total = (total + int(digit)) * 2
    check = (12 - total % 11) % 11
    if digits[-1] != ("X" if check == 10 else str(check)):
        raise ValueError("This ORCID iD has an invalid checksum.")
    return identifier


def suggest_topics(titles):
    counts = Counter()
    for title in titles:
        tokens = normalized_words(title).split()
        seen = set()
        for first, second in zip(tokens, tokens[1:]):
            if first not in STOP and second not in STOP and all(len(w) > 2 and not w.isdigit() for w in (first, second)):
                seen.add(f"{first} {second}")
        counts.update(seen)
    suggestions, used = [], set()
    for phrase, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        words = set(phrase.split())
        if words & used or len(phrase) > 70:
            continue
        suggestions.append(phrase)
        used.update(words)
        if len(suggestions) == 8:
            break
    return suggestions


def read_orcid(identifier):
    headers = {"Accept": "application/vnd.orcid+json", "User-Agent": "Conference/0.2"}
    if token := os.getenv("CONFERENCE_ORCID_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"https://pub.orcid.org/v3.0/{identifier}/record", headers=headers)
    try:
        with urlopen(request, timeout=12) as response:
            raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise ValueError("The ORCID record exceeds the import size limit.")
            data = json.loads(raw)
    except HTTPError as error:
        if error.code == 404:
            raise ValueError("ORCID could not find this public profile.") from error
        if error.code in (401, 403):
            raise ValueError("ORCID requires access credentials for this request. Set CONFERENCE_ORCID_TOKEN.") from error
        raise
    person = data.get("person") or {}
    name = person.get("name") or {}
    label = (name.get("credit-name") or {}).get("value") or " ".join(
        (name.get(key) or {}).get("value") or "" for key in ("given-names", "family-name")
    ).strip()
    works = []
    for group in ((data.get("activities-summary") or {}).get("works") or {}).get("group", []):
        for work in group.get("work-summary") or []:
            title = clean((((work.get("title") or {}).get("title") or {}).get("value")))[:300]
            if not title:
                continue
            identifiers = ((work.get("external-ids") or {}).get("external-id") or [])
            doi = next((normalized_doi(item.get("external-id-value") or "") for item in identifiers
                        if item.get("external-id-type") == "doi" and item.get("external-id-relationship") == "self"), "")
            year = (((work.get("publication-date") or {}).get("year") or {}).get("value")) or ""
            works.append({"title": title, "doi": doi, "year": year})
            break
    works.sort(key=lambda work: work["year"], reverse=True)
    return {"id": identifier, "name": label, "works": works[:40],
            "suggestions": suggest_topics(work["title"] for work in works[:20]),
            "total": len(works)}


def config(payload):
    topic_list = payload.get("topics")
    if not isinstance(topic_list, str) or len(topic_list) > 600:
        raise ValueError("Enter a comma-separated list of research topics.")
    topics, seen = [], set()
    for value in topic_list.split(","):
        topic = " ".join(value.split())
        key = normalized_words(topic)
        if not topic or key in seen:
            continue
        if not key or len(topic) > 70 or any(not (c.isalnum() or c in " -–+._()/") for c in topic):
            raise ValueError("Each topic must be a short phrase of up to 70 characters.")
        seen.add(key)
        topics.append(topic)
    if len(topics) > 8:
        raise ValueError("Follow up to eight topics.")
    days = int(payload.get("days", 30))
    if not 1 <= days <= 365:
        raise ValueError("Choose a lookback from 1 to 365 days.")
    address = str(payload.get("email", "")).strip()
    frequency = payload.get("frequency", "off")
    if frequency not in {"off", "daily", "weekly"}:
        raise ValueError("Choose a daily or weekly digest, or turn it off.")
    if (address or frequency != "off") and (len(address) > 254 or not re.fullmatch(EMAIL_PATTERN, address)):
        raise ValueError("Enter a valid email address for delivery.")
    return {"topics": topics, "days": days, "email": address, "frequency": frequency,
            "auto_refresh": payload.get("auto_refresh") is True}


def match_feed(records, topics, profile):
    works = (profile or {}).get("works", [])
    own_dois = {work["doi"] for work in works if work["doi"]}
    own_titles = {normalized_words(work["title"]) for work in works}
    topic_sources = {}
    for topic in topics:
        phrase = f" {normalized_words(topic)} "
        topic_sources[topic] = next((work["title"] for work in works
                                     if phrase in f" {normalized_words(work['title'])} "), "")
    output = {}
    for record in records:
        title = normalized_words(record["title"])
        doi = normalized_doi(record["doi"])
        if doi in own_dois or title in own_titles:
            continue
        body = normalized_words(record["text"])
        reasons = []
        for topic in topics:
            needle = f" {normalized_words(topic)} "
            if needle in f" {title} ":
                where = "Title"
            elif needle in f" {body} ":
                where = "Description"
            else:
                continue
            reasons.append({"topic": topic, "where": where, "work": topic_sources[topic]})
        if not reasons:
            continue
        key = (f"zenodo:{record['version_group']}" if record["version_group"]
               else f"doi:{doi}" if doi else record["id"])
        score = sum((3 if reason["where"] == "Title" else 1) * min(3, len(reason["topic"].split()))
                    for reason in reasons)
        kind = "Preprint" if record["id"].startswith("PPR:") else record["kind"]
        entry = {**record, "key": key, "kind": kind, "reasons": reasons, "score": score}
        prior = output.get(key)
        if prior is None or (entry["date"], entry["score"]) > (prior["date"], prior["score"]):
            output[key] = entry
    return sorted(output.values(), key=lambda item: (item["score"], item["date"], item["key"]), reverse=True)


def scan(settings, profile):
    start, end = dates(settings["days"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(fn, settings["topics"], start, end, any_topic=True)
                   for fn in (papers_feed, zenodo)]
        statuses, records = [], []
        for source, future in zip(("Europe PMC", "Zenodo"), results):
            try:
                rows, available, note = future.result()
                records.extend(rows)
                statuses.append({"source": "PubMed" if note else source, "retrieved": len(rows),
                                 "available": available, "note": note, "ok": True})
            except SOURCE_ERRORS as error:
                statuses.append({"source": source, "note": str(error), "ok": False})
    return match_feed(records, settings["topics"], profile), statuses


def example_feed():
    examples = []
    for kind, source, title, desc, topic in (
        ("Preprint", "Europe PMC", "Comparing leaf cell states across Arabidopsis drought studies",
         "A fictional preprint comparing leaf cell states across three public Arabidopsis cohorts.", "Arabidopsis drought"),
        ("Dataset", "Zenodo", "Arabidopsis drought RNA-seq and sample annotations",
         "A fictional release with expression matrices and a sample manifest covering treatment and controls.", "Arabidopsis drought"),
        ("Paper", "Europe PMC", "Tracking seasonal shifts in single-cell plant atlases",
         "A fictional paper describing how seasonal sampling changes observed cell-state proportions.", "single-cell plant atlases"),
    ):
        examples.append({"key": title, "kind": kind, "source": source, "title": title, "text": desc,
                         "reasons": [{"topic": topic, "where": "Title"}], "url": "", "date": "", "read": False,
                         "files": []})
    return examples


def smtp_options():
    host = os.getenv("CONFERENCE_SMTP_HOST")
    sender = os.getenv("CONFERENCE_SMTP_FROM")
    return (host, sender) if host and sender else None


def digest_text(items):
    lines = ["Your Conference research digest", "", f"{len(items)} new matches.", ""]
    for item in items:
        lines += [f"{item['kind']} · {item['title']}",
                  "Matched topics: " + ", ".join(reason["topic"] for reason in item["reasons"]),
                  item["url"], ""]
    return "\n".join(lines)


def send_digest(address, items):
    host, sender = smtp_options()
    tls_context = ssl.create_default_context()
    mode = os.getenv("CONFERENCE_SMTP_TLS", "starttls")
    if mode not in {"starttls", "ssl"}:
        raise ValueError("Use starttls or ssl for CONFERENCE_SMTP_TLS.")
    port = int(os.getenv("CONFERENCE_SMTP_PORT", "465" if mode == "ssl" else "587"))
    client = (smtplib.SMTP_SSL(host, port, timeout=12, context=tls_context) if mode == "ssl"
              else smtplib.SMTP(host, port, timeout=12))
    with client:
        if mode == "starttls":
            client.starttls(context=tls_context)
        if user := os.getenv("CONFERENCE_SMTP_USERNAME"):
            client.login(user, os.getenv("CONFERENCE_SMTP_PASSWORD", ""))
        message = EmailMessage()
        message["From"], message["To"] = sender, address
        message["Subject"] = f"Conference · {len(items)} new research matches"
        message.set_content(digest_text(items))
        if client.send_message(message):
            raise smtplib.SMTPException("SMTP did not accept the recipient.")


class Store:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS feed (
                scope TEXT NOT NULL, key TEXT NOT NULL, item TEXT NOT NULL,
                first_seen REAL NOT NULL, read INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (scope, key)
              );
              CREATE TABLE IF NOT EXISTS delivered (
                address TEXT NOT NULL, key TEXT NOT NULL, PRIMARY KEY (address, key)
              );
              CREATE TABLE IF NOT EXISTS last_delivery (address TEXT PRIMARY KEY, sent REAL NOT NULL);
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=20)

    @staticmethod
    def get(db, name, default=None):
        row = db.execute("SELECT value FROM metadata WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def put(db, name, value):
        db.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (name, json.dumps(value)))

    def save(self, name, value):
        with self.connect() as db:
            self.put(db, name, value)

    @staticmethod
    def scope(settings, profile):
        return json.dumps([(profile or {}).get("id"), sorted(normalized_words(t) for t in settings["topics"])])

    def state(self):
        with self.connect() as db:
            settings = self.get(db, "settings", DEFAULT)
            profile = self.get(db, "profile")
            scope = self.scope(settings, profile)
            report = self.get(db, "scan:" + scope, {})
            rows = db.execute("""SELECT item, read, first_seen FROM feed WHERE scope=?
                                ORDER BY first_seen DESC LIMIT 160""", (scope,)).fetchall()
            mail = self.get(db, "mail:" + settings["email"], {})
        feed = [{**json.loads(item), "read": bool(read), "first_seen": seen} for item, read, seen in rows]
        feed.sort(key=lambda item: (item["first_seen"], item["score"], item["date"]), reverse=True)
        return {"settings": settings, "profile": profile, "report": report,
                "feed": feed if settings["topics"] else example_feed(), "demo": not settings["topics"],
                "mail": {**mail, "configured": bool(smtp_options())}}

    def refresh(self):
        with self.connect() as db:
            settings = self.get(db, "settings", DEFAULT)
            profile = self.get(db, "profile")
        if not settings["topics"]:
            return
        scope = self.scope(settings, profile)
        matches, statuses = scan(settings, profile)
        now = time.time()
        with self.connect() as db:
            self.put(db, "scan:" + scope, {"time": now, "sources": statuses, "matches": len(matches)})
            db.executemany("""INSERT INTO feed(scope, key, item, first_seen) VALUES(?, ?, ?, ?)
                              ON CONFLICT(scope,key) DO UPDATE SET item=excluded.item""",
                           [(scope, item["key"], json.dumps(item), now) for item in matches])

    def mark(self, key):
        with self.connect() as db:
            scope = self.scope(self.get(db, "settings", DEFAULT), self.get(db, "profile"))
            db.execute("UPDATE feed SET read=1 WHERE scope=? AND key=?", (scope, key))

    def pending(self, db, settings):
        scope = self.scope(settings, self.get(db, "profile"))
        rows = db.execute("""
          SELECT f.item FROM feed f LEFT JOIN delivered d ON d.address=? AND d.key=f.key
          WHERE f.scope=? AND d.key IS NULL ORDER BY f.first_seen DESC LIMIT 20
        """, (settings["email"].lower(), scope)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def preview(self):
        with self.connect() as db:
            items = self.pending(db, self.get(db, "settings", DEFAULT))
        return {"count": len(items), "text": digest_text(items)}

    def deliver(self):
        if not smtp_options():
            return
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            settings = self.get(db, "settings", DEFAULT)
            if settings["frequency"] == "off":
                return
            address = settings["email"].lower()
            row = db.execute("SELECT sent FROM last_delivery WHERE address=?", (address,)).fetchone()
            now = time.time()
            if row and now - row[0] < DAY * (7 if settings["frequency"] == "weekly" else 1):
                return
            items = self.pending(db, settings)
            if not items:
                return
            try:
                send_digest(settings["email"], items)
            except (OSError, ValueError, smtplib.SMTPException):
                self.put(db, "mail:" + settings["email"],
                         {"status": "Delivery failed. Check SMTP settings. Matches are still pending.", "time": now})
                logging.warning("Conference digest delivery failed. Check SMTP settings.")
                return
            db.executemany("INSERT OR IGNORE INTO delivered VALUES (?, ?)", [(address, item["key"]) for item in items])
            db.execute("INSERT OR REPLACE INTO last_delivery VALUES (?, ?)", (address, now))
            self.put(db, "mail:" + settings["email"], {"status": "Digest sent", "time": now})


def make_handler(store, port, refresh_lock):
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def respond(self, payload, status=200, content_type="application/json; charset=utf-8"):
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.headers.get("Host") not in allowed:
                return self.respond({"error": "Use the local address shown in the terminal."}, 403)
            assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                      "/style.css": ("style.css", "text/css")}
            if self.path in assets:
                name, mime = assets[self.path]
                return self.respond((ROOT / name).read_bytes(), content_type=mime + "; charset=utf-8")
            if self.path == "/api/state":
                return self.respond(store.state())
            if self.path == "/api/digest":
                return self.respond(store.preview())
            return self.respond({"error": "Page not found."}, 404)

        def do_POST(self):
            host = self.headers.get("Host")
            if host not in allowed or self.headers.get("Origin") != f"http://{host}":
                return self.respond({"error": "Use the local dashboard to make changes."}, 403)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8192 or self.headers.get_content_type() != "application/json":
                    raise ValueError("Expected a small JSON request.")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object.")
                if self.path == "/api/settings":
                    store.save("settings", config(data))
                elif self.path == "/api/orcid":
                    store.save("profile", read_orcid(orcid_id(str(data.get("id", "")))))
                elif self.path == "/api/disconnect":
                    store.save("profile", None)
                elif self.path == "/api/refresh":
                    if not refresh_lock.acquire(blocking=False):
                        return self.respond({"error": "A feed refresh is already running."}, 409)
                    try:
                        store.refresh()
                    finally:
                        refresh_lock.release()
                elif self.path == "/api/read":
                    store.mark(str(data.get("key", "")))
                else:
                    return self.respond({"error": "Page not found."}, 404)
            except (TypeError, ValueError) as error:
                return self.respond({"error": str(error)}, 400)
            except SOURCE_ERRORS:
                return self.respond({"error": "The source is unavailable. Try again shortly."}, 502)
            return self.respond(store.state())

    return Handler


def run_background(store, lock, stopped):
    while not stopped.is_set():
        try:
            state = store.state()
            settings = state["settings"]
            last = state["report"].get("time", 0)
            if settings["topics"] and settings["auto_refresh"] and time.time() - last >= DAY:
                if lock.acquire(blocking=False):
                    try:
                        store.refresh()
                    finally:
                        lock.release()
            store.deliver()
        except (OSError, sqlite3.Error):
            logging.exception("Conference background check failed")
        stopped.wait(300)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=ROOT / "conference.sqlite3")
    parser.add_argument("command", nargs="?", choices=("serve", "refresh", "digest"), default="serve")
    args = parser.parse_args()
    store = Store(args.db)
    if args.command == "refresh":
        store.refresh()
        print(json.dumps(store.state()["report"], indent=2))
    elif args.command == "digest":
        print(store.preview()["text"])
    else:
        stop, lock = threading.Event(), threading.Lock()
        with ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store, args.port, lock)) as httpd:
            threading.Thread(target=run_background, args=(store, lock, stop), daemon=True).start()
            print(f"Conference · http://127.0.0.1:{args.port}", flush=True)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                stop.set()


if __name__ == "__main__":
    main()
