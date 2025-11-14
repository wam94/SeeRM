import base64
import re
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_URI = "https://oauth2.googleapis.com/token"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def build_service(client_id: str, client_secret: str, refresh_token: str):
    creds = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def search_messages(service, user_id: str, query: str, max_results: int = 10):
    resp = (
        service.users().messages().list(userId=user_id, q=query, maxResults=max_results).execute()
    )
    return resp.get("messages", [])


def get_message(service, user_id: str, msg_id: str):
    return service.users().messages().get(userId=user_id, id=msg_id, format="full").execute()


def extract_csv_attachments(service, user_id: str, message: dict, attachment_regex: str):
    out = []
    payload = message.get("payload", {})
    parts = payload.get("parts", []) or []
    pattern = re.compile(attachment_regex)
    for part in parts:
        filename = part.get("filename")
        if not filename or not pattern.match(filename):
            continue
        body = part.get("body", {})
        attach_id = body.get("attachmentId")
        if not attach_id:
            data = body.get("data")
            if data:
                out.append((filename, base64.urlsafe_b64decode(data)))
            continue
        att = (
            service.users()
            .messages()
            .attachments()
            .get(userId=user_id, messageId=message["id"], id=attach_id)
            .execute()
        )
        data = base64.urlsafe_b64decode(att["data"])
        out.append((filename, data))
    return out


def send_html_email(
    service,
    user_id: str,
    to: str,
    subject: str,
    html: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
):
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["From"] = user_id
    msg["Subject"] = subject
    msg.set_content("HTML email. View in an HTML-capable client.")
    msg.add_alternative(html, subtype="html")

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return service.users().messages().send(userId=user_id, body={"raw": raw}).execute()
