#!/usr/bin/env python3
"""Gitea-backed comment system for static sites.

Each page's slug maps to a Gitea issue, and that issue's comments are the
comment store -- Gitea becomes the database, with no separate comments
backend to run or back up. This Flask service holds the Gitea API token
server-side, so the browser never sees a credential; the site only ever
talks to this bridge, over GET/POST /comments/<slug>.

Full write-up, including the nginx proxy config and frontend shortcode
that pairs with this: https://hillnet.co.nz/projects/self-hosted-hugo-comments-private-gitea/
"""
import logging
import os
import smtplib
from email.message import EmailMessage

import requests
from flask import Flask, jsonify, request
from markdown import markdown

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")

# ----------------------------
# Required: where comments live
# ----------------------------
def _required(name):
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"{name} must be set (see README.md)")
    return val

GITEA_URL = _required("GITEA_URL").rstrip("/")     # e.g. https://gitea.example.com/api/v1
REPO_OWNER = _required("REPO_OWNER")
REPO_NAME = _required("REPO_NAME")
API_TOKEN = _required("GITEA_TOKEN")                # needs repo issue read/write

HEADERS = {
    "Authorization": f"token {API_TOKEN}",
    "Content-Type": "application/json",
}

# ----------------------------
# Optional: site name used in alert subject lines / fallback links
# ----------------------------
SITE_NAME = os.environ.get("SITE_NAME", REPO_NAME)
SITE_URL = os.environ.get("SITE_URL", "")

# ----------------------------
# Optional: e-mail notification on a new comment. Unset MAIL_RELAY_HOST to
# disable notifications entirely - everything else still works.
# ----------------------------
MAIL_RELAY_HOST = os.environ.get("MAIL_RELAY_HOST")
MAIL_RELAY_PORT = int(os.environ.get("MAIL_RELAY_PORT", "25"))
MAIL_FROM = os.environ.get("MAIL_FROM", f"comments@{SITE_NAME}")
MAIL_TO = os.environ.get("MAIL_TO")


def notify_new_comment(slug, body_text, comment_url, post_url=None):
    """Best-effort email alert for a new comment. Never raises - a mail
    failure must not block the comment itself from succeeding."""
    if not MAIL_RELAY_HOST or not MAIL_TO:
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = f"New comment on {SITE_NAME}: {slug}"
        msg["From"] = MAIL_FROM
        msg["To"] = MAIL_TO
        post_url = post_url or (f"{SITE_URL}/ (slug: {slug})" if SITE_URL else f"slug: {slug}")
        msg.set_content(
            f"New comment on \"{slug}\":\n\n"
            f"{body_text}\n\n"
            f"Post: {post_url}\n"
            f"Moderate/reply on Gitea: {comment_url}\n"
        )
        with smtplib.SMTP(MAIL_RELAY_HOST, MAIL_RELAY_PORT, timeout=10) as smtp:
            smtp.send_message(msg)
        logging.debug(f"Notification email sent for slug={slug}")
    except Exception:
        logging.exception(f"Failed to send notification email for slug={slug}")


# ----------------------------
# Simple moderation function - extend this however you like
# ----------------------------
def moderate_comment(text):
    banned_words = ["spamword1", "spamword2"]
    for word in banned_words:
        if word.lower() in text.lower():
            return False
    return True


# ----------------------------
# Helper: find or create the Gitea issue for a slug
# ----------------------------
def get_or_create_issue(slug):
    logging.debug(f"Searching for issue with title: {slug}")
    url = f"{GITEA_URL}/repos/{REPO_OWNER}/{REPO_NAME}/issues"
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    issues = resp.json()

    for issue in issues:
        if issue["title"] == slug:
            logging.debug(f"Issue found: {issue['html_url']}")
            return issue["number"]

    logging.debug(f"Issue not found, creating new issue for slug: {slug}")
    payload = {"title": slug, "body": f"Auto-generated issue for {slug}"}
    resp = requests.post(url, headers=HEADERS, json=payload)
    resp.raise_for_status()
    issue = resp.json()
    logging.debug(f"Issue created: {issue['html_url']}")
    return issue["number"]


# ----------------------------
# Helper: post a comment to Gitea
# ----------------------------
def post_to_gitea(slug, body_text, post_url=None):
    if not moderate_comment(body_text):
        logging.warning(f"Comment blocked by moderation: {body_text}")
        return {"error": "Comment blocked by moderation"}

    issue_number = get_or_create_issue(slug)
    url = f"{GITEA_URL}/repos/{REPO_OWNER}/{REPO_NAME}/issues/{issue_number}/comments"
    payload = {"body": body_text}
    resp = requests.post(url, headers=HEADERS, json=payload)
    resp.raise_for_status()
    comment = resp.json()
    logging.debug(f"Comment posted to Gitea: {comment['html_url']}")
    notify_new_comment(slug, body_text, comment["html_url"], post_url)
    return comment


# ----------------------------
# Flask app
# ----------------------------
app = Flask("bridge")


@app.route("/comments/<slug>", methods=["GET"])
def get_comments(slug):
    logging.debug(f"GET /comments/{slug}")
    url = f"{GITEA_URL}/repos/{REPO_OWNER}/{REPO_NAME}/issues"
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    issues = resp.json()

    issue_number = None
    for issue in issues:
        if issue["title"] == slug:
            issue_number = issue["number"]
            break
    if not issue_number:
        return jsonify([])

    url = f"{GITEA_URL}/repos/{REPO_OWNER}/{REPO_NAME}/issues/{issue_number}/comments"
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    comments = resp.json()

    for c in comments:
        c["body"] = markdown(c["body"])
    return jsonify(comments)


@app.route("/comments/<slug>", methods=["POST"])
def post_comment(slug):
    data = request.get_json()
    logging.debug(f"Received POST for slug={slug}, body={data}")

    if not data or "body" not in data:
        return jsonify({"error": "Missing 'body' in request"}), 400

    try:
        comment = post_to_gitea(slug, data["body"], post_url=request.referrer)
        if "error" in comment:
            return jsonify(comment), 403
        return jsonify(comment), 200
    except requests.HTTPError as e:
        logging.exception("HTTP error posting to Gitea")
        return jsonify({"error": str(e), "status_code": e.response.status_code}), e.response.status_code
    except Exception as e:
        logging.exception("Unexpected error posting to Gitea")
        return jsonify({"error": str(e)}), 500


# ----------------------------
# Run Flask (development only - put a real WSGI server/reverse proxy in front)
# ----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
