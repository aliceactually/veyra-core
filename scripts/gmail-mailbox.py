#!/usr/bin/env python3
"""Least-privilege OAuth access to one Gmail mailbox.

Credentials are retained in Veyra's encrypted vault. The local configuration
contains only the mailbox address, preferred send-as alias, and opaque vault ID.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
from email.message import EmailMessage
import hashlib
import http.server
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser


REPO = Path(__file__).resolve().parents[1]
PRIVATE = REPO / ".private"
DEFAULT_CONFIG = PRIVATE / "gmail-mailbox.json"
VAULT_SCRIPT = REPO / "scripts" / "veyra-vault.py"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GOOGLE_AUTH_URIS = {
    "https://accounts.google.com/o/oauth2/auth",
    "https://accounts.google.com/o/oauth2/v2/auth",
}
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
DEFAULT_TIMEOUT_SECONDS = 300


class GmailError(RuntimeError):
    pass


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def json_request(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    data: dict[str, object] | None = None,
    form: dict[str, str] | None = None,
) -> dict[str, object]:
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif form is not None:
        body = urllib.parse.urlencode(form).encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            message = parsed.get("error", {}).get("message") or detail
        except (json.JSONDecodeError, AttributeError):
            message = detail
        raise GmailError(f"Google API returned HTTP {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise GmailError(f"Could not reach Google: {exc.reason}") from exc
    if not payload:
        return {}
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise GmailError("Google returned an invalid JSON response") from exc
    if not isinstance(value, dict):
        raise GmailError("Google returned a non-object response")
    return value


def parse_client_json(value: bytes) -> dict[str, str]:
    try:
        document = json.loads(value)
        client = document["installed"]
        parsed = {
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "auth_uri": client.get("auth_uri", "https://accounts.google.com/o/oauth2/v2/auth"),
            "token_uri": client.get("token_uri", "https://oauth2.googleapis.com/token"),
        }
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise GmailError("Expected a Google OAuth Desktop app client JSON document") from exc
    if not all(isinstance(item, str) and item for item in parsed.values()):
        raise GmailError("OAuth client JSON contains an invalid field")
    if parsed["auth_uri"] not in GOOGLE_AUTH_URIS:
        raise GmailError("OAuth client JSON contains a non-Google authorisation endpoint")
    if parsed["token_uri"] != GOOGLE_TOKEN_URI:
        raise GmailError("OAuth client JSON contains a non-Google token endpoint")
    if not parsed["client_id"].endswith(".apps.googleusercontent.com"):
        raise GmailError("OAuth client JSON contains an invalid Google client ID")
    return parsed


def build_authorisation_url(
    client: dict[str, str],
    *,
    redirect_uri: str,
    state: str,
    verifier: str,
    account: str,
) -> str:
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    query = urllib.parse.urlencode(
        {
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GMAIL_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "false",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "login_hint": account,
        }
    )
    return f"{client['auth_uri']}?{query}"


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    callback: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.__class__.callback = {key: values[0] for key, values in query.items() if values}
        success = "code" in self.__class__.callback
        self.send_response(200 if success else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        outcome = "Authorisation received. You may close this tab." if success else "Authorisation failed. Return to the terminal."
        self.wfile.write(f"<!doctype html><title>Veyra Gmail</title><p>{outcome}</p>".encode())

    def log_message(self, _format: str, *_args: object) -> None:
        pass


def wait_for_authorisation(
    client: dict[str, str], account: str, timeout: int, open_browser: bool
) -> tuple[str, str, str]:
    CallbackHandler.callback = {}
    server = http.server.HTTPServer(("127.0.0.1", 0), CallbackHandler)
    server.timeout = timeout
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    url = build_authorisation_url(
        client,
        redirect_uri=redirect_uri,
        state=state,
        verifier=verifier,
        account=account,
    )
    print("Open this Google authorisation URL in your browser:", file=sys.stderr)
    print(url, file=sys.stderr)
    if open_browser:
        webbrowser.open(url)
    server.handle_request()
    server.server_close()
    callback = CallbackHandler.callback
    if callback.get("state") != state:
        raise GmailError("OAuth callback state was missing or invalid")
    if "error" in callback:
        raise GmailError(f"Google authorisation failed: {callback['error']}")
    code = callback.get("code")
    if not code:
        raise GmailError("Timed out waiting for Google authorisation")
    return code, verifier, redirect_uri


def exchange_code(
    client: dict[str, str], code: str, verifier: str, redirect_uri: str
) -> dict[str, object]:
    return json_request(
        client["token_uri"],
        method="POST",
        form={
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
    )


def atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def store_credentials(
    secret: dict[str, object], authorisation: str, account: str
) -> str:
    command = [
        sys.executable,
        str(VAULT_SCRIPT),
        "put-stdin",
        "--name",
        "Google Workspace Gmail OAuth credential",
        "--kind",
        "Google OAuth desktop client and refresh token",
        "--purpose",
        "Read Alice's work Gmail and perform explicitly approved send or trash actions",
        "--scope",
        f"{account} via gmail.modify",
        "--fingerprint",
        f"client_id:{secret['client_id']}",
        "--authorisation",
        authorisation,
    ]
    stored = subprocess.run(
        command,
        input=json.dumps(secret, separators=(",", ":")).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if stored.returncode:
        raise GmailError("Vault retention failed; no credential value was displayed")
    words = stored.stdout.decode("ascii", errors="strict").split()
    if len(words) < 3 or len(words[2].rstrip(":")) != 32:
        raise GmailError("Vault returned an invalid credential receipt")
    return words[2].rstrip(":")


def load_config(path: Path) -> dict[str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        config = {key: value[key] for key in ("account", "from_alias", "credential_id")}
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise GmailError(f"Invalid or missing Gmail configuration: {path}") from exc
    if not all(isinstance(item, str) and item for item in config.values()):
        raise GmailError(f"Invalid Gmail configuration: {path}")
    return config


def decrypt_credentials(identifier: str) -> dict[str, str]:
    config_root = Path(
        os.environ.get(
            "VEYRA_CORE_CONFIG_DIR",
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "veyra-core",
        )
    ).expanduser()
    identity = config_root / "vault-identity.txt"
    encrypted = REPO / "vault" / "entries" / identifier / "secret.age"
    if not identity.is_file() or not encrypted.is_file():
        raise GmailError("Configured Gmail credential is unavailable in the active vault")
    decrypted = subprocess.run(
        ["age", "-d", "-i", str(identity), str(encrypted)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if decrypted.returncode:
        raise GmailError("Could not decrypt the configured Gmail credential")
    try:
        value = json.loads(decrypted.stdout)
        credential = {
            key: value[key]
            for key in ("client_id", "client_secret", "refresh_token", "token_uri")
        }
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise GmailError("Stored Gmail credential is invalid") from exc
    if not all(isinstance(item, str) and item for item in credential.values()):
        raise GmailError("Stored Gmail credential contains an invalid field")
    if credential["token_uri"] != GOOGLE_TOKEN_URI:
        raise GmailError("Stored Gmail credential contains a non-Google token endpoint")
    return credential


class GmailSession:
    def __init__(self, config_path: Path):
        self.config = load_config(config_path)
        credential = decrypt_credentials(self.config["credential_id"])
        token = json_request(
            credential["token_uri"],
            method="POST",
            form={
                "client_id": credential["client_id"],
                "client_secret": credential["client_secret"],
                "refresh_token": credential["refresh_token"],
                "grant_type": "refresh_token",
            },
        ).get("access_token")
        if not isinstance(token, str) or not token:
            raise GmailError("Google did not return an access token")
        self.token = token

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        query: dict[str, str | int] | None = None,
        data: dict[str, object] | None = None,
    ) -> dict[str, object]:
        url = f"{GMAIL_API}/{path.lstrip('/')}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return json_request(url, method=method, token=self.token, data=data)


def header_map(payload: dict[str, object]) -> dict[str, str]:
    headers = payload.get("headers", [])
    if not isinstance(headers, list):
        return {}
    result = {}
    for item in headers:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            result[item["name"].lower()] = str(item.get("value", ""))
    return result


def extract_body(payload: dict[str, object]) -> tuple[str, str]:
    candidates: list[tuple[str, str]] = []

    def walk(part: dict[str, object]) -> None:
        mime = str(part.get("mimeType", ""))
        body = part.get("body", {})
        if isinstance(body, dict) and isinstance(body.get("data"), str):
            try:
                text = b64url_decode(body["data"]).decode("utf-8", errors="replace")
                candidates.append((mime, text))
            except (ValueError, TypeError):
                pass
        parts = part.get("parts", [])
        if isinstance(parts, list):
            for child in parts:
                if isinstance(child, dict):
                    walk(child)

    walk(payload)
    for preferred in ("text/plain", "text/html"):
        for mime, text in candidates:
            if mime == preferred:
                return mime, text
    return "", ""


def message_summary(message: dict[str, object]) -> dict[str, object]:
    payload = message.get("payload", {})
    headers = header_map(payload if isinstance(payload, dict) else {})
    return {
        "id": message.get("id"),
        "thread_id": message.get("threadId"),
        "labels": message.get("labelIds", []),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "date": headers.get("date", ""),
        "subject": headers.get("subject", ""),
        "snippet": message.get("snippet", ""),
    }


def require_authorisation(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 500:
        raise GmailError("A concise authenticated-chat authorisation is required")
    return value


def send_alias(session: GmailSession) -> dict[str, object]:
    result = session.request("settings/sendAs")
    aliases = result.get("sendAs", [])
    for alias in aliases if isinstance(aliases, list) else []:
        if (
            isinstance(alias, dict)
            and str(alias.get("sendAsEmail", "")).lower()
            == session.config["from_alias"].lower()
            and alias.get("verificationStatus", "accepted") == "accepted"
        ):
            return alias
    raise GmailError(
        f"Preferred From alias is not accepted: {session.config['from_alias']}"
    )


def public_aliases(response: dict[str, object]) -> list[dict[str, object]]:
    result = []
    aliases = response.get("sendAs", [])
    for alias in aliases if isinstance(aliases, list) else []:
        if not isinstance(alias, dict):
            continue
        result.append(
            {
                "email": str(alias.get("sendAsEmail", "")),
                "verification_status": alias.get("verificationStatus", "accepted"),
                "primary": bool(alias.get("isPrimary", False)),
                "default": bool(alias.get("isDefault", False)),
            }
        )
    return result


def command_authorize(args: argparse.Namespace) -> None:
    source = Path(args.client_json)
    try:
        client_bytes = source.read_bytes()
    except OSError as exc:
        raise GmailError(f"Could not read OAuth client JSON: {source}") from exc
    client = parse_client_json(client_bytes)
    code, verifier, redirect_uri = wait_for_authorisation(
        client, args.account, args.timeout, not args.no_browser
    )
    token = exchange_code(client, code, verifier, redirect_uri)
    access_token = token.get("access_token")
    refresh_token = token.get("refresh_token")
    if not isinstance(access_token, str) or not isinstance(refresh_token, str):
        raise GmailError("Google did not issue the required access and refresh tokens")
    profile = json_request(f"{GMAIL_API}/profile", token=access_token)
    actual = str(profile.get("emailAddress", ""))
    if actual.lower() != args.account.lower():
        raise GmailError(f"Authorised the wrong mailbox: {actual or 'unknown'}")
    aliases = json_request(f"{GMAIL_API}/settings/sendAs", token=access_token)
    alias_details = public_aliases(aliases)
    accepted = {
        str(item["email"]).lower()
        for item in alias_details
        if item["verification_status"] == "accepted"
    }
    alias_ready = args.from_alias.lower() in accepted
    secret = {
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "refresh_token": refresh_token,
        "token_uri": client["token_uri"],
        "scope": GMAIL_SCOPE,
    }
    identifier = store_credentials(
        secret, require_authorisation(args.authorisation), args.account
    )
    atomic_json(
        args.config,
        {
            "account": args.account,
            "from_alias": args.from_alias,
            "credential_id": identifier,
            "configured_at": now(),
        },
    )
    print(
        json.dumps(
            {
                "status": "configured",
                "account": actual,
                "from_alias": args.from_alias,
                "from_alias_ready": alias_ready,
                "available_aliases": alias_details,
                "credential_id": identifier,
            },
            indent=2,
        )
    )


def command_status(args: argparse.Namespace) -> None:
    session = GmailSession(args.config)
    profile = session.request("profile")
    aliases = public_aliases(session.request("settings/sendAs"))
    preferred = session.config["from_alias"].lower()
    alias_ready = any(
        str(alias["email"]).lower() == preferred
        and alias["verification_status"] == "accepted"
        for alias in aliases
    )
    print(
        json.dumps(
            {
                "account": profile.get("emailAddress"),
                "messages_total": profile.get("messagesTotal"),
                "threads_total": profile.get("threadsTotal"),
                "from_alias": session.config["from_alias"],
                "from_alias_ready": alias_ready,
                "available_aliases": aliases,
            },
            indent=2,
        )
    )


def command_search(args: argparse.Namespace) -> None:
    session = GmailSession(args.config)
    result = session.request("messages", query={"q": args.query, "maxResults": args.limit})
    summaries = []
    for item in result.get("messages", []) if isinstance(result.get("messages", []), list) else []:
        identifier = item.get("id") if isinstance(item, dict) else None
        if isinstance(identifier, str):
            message = session.request(
                f"messages/{identifier}", query={"format": "metadata"}
            )
            summaries.append(message_summary(message))
    print(json.dumps({"query": args.query, "messages": summaries}, indent=2))


def command_read(args: argparse.Namespace) -> None:
    session = GmailSession(args.config)
    message = session.request(f"messages/{args.message_id}", query={"format": "full"})
    payload = message.get("payload", {})
    mime, body = extract_body(payload if isinstance(payload, dict) else {})
    result = message_summary(message)
    result.update({"body_mime_type": mime, "body": body})
    print(json.dumps(result, indent=2))


def command_send(args: argparse.Namespace) -> None:
    authorisation = require_authorisation(args.authorisation)
    session = GmailSession(args.config)
    alias = send_alias(session)
    message = EmailMessage()
    message["From"] = str(alias["sendAsEmail"])
    message["To"] = args.to
    if args.cc:
        message["Cc"] = args.cc
    if args.bcc:
        message["Bcc"] = args.bcc
    message["Subject"] = args.subject
    message.set_content(args.body)
    sent = session.request(
        "messages/send",
        method="POST",
        data={"raw": b64url(message.as_bytes())},
    )
    print(json.dumps({"status": "sent", "id": sent.get("id"), "thread_id": sent.get("threadId"), "from": message["From"], "to": args.to, "subject": args.subject, "authorisation": authorisation}, indent=2))


def command_trash(args: argparse.Namespace) -> None:
    authorisation = require_authorisation(args.authorisation)
    session = GmailSession(args.config)
    result = session.request(f"messages/{args.message_id}/trash", method="POST", data={})
    print(json.dumps({"status": "trashed", "id": result.get("id", args.message_id), "authorisation": authorisation}, indent=2))


def command_untrash(args: argparse.Namespace) -> None:
    authorisation = require_authorisation(args.authorisation)
    session = GmailSession(args.config)
    result = session.request(f"messages/{args.message_id}/untrash", method="POST", data={})
    print(json.dumps({"status": "restored", "id": result.get("id", args.message_id), "authorisation": authorisation}, indent=2))


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = value.add_subparsers(dest="command", required=True)

    authorize = commands.add_parser("authorize", help="Complete one-user OAuth consent")
    authorize.add_argument("--client-json", required=True, help="Google Desktop OAuth client JSON file or FIFO")
    authorize.add_argument("--account", required=True)
    authorize.add_argument("--from-alias", required=True)
    authorize.add_argument("--authorisation", required=True)
    authorize.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    authorize.add_argument("--no-browser", action="store_true")
    authorize.set_defaults(function=command_authorize)

    status = commands.add_parser("status", help="Verify mailbox and preferred alias")
    status.set_defaults(function=command_status)

    search = commands.add_parser("search", help="Search message metadata")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10, choices=range(1, 101))
    search.set_defaults(function=command_search)

    read = commands.add_parser("read", help="Read one message body")
    read.add_argument("message_id")
    read.set_defaults(function=command_read)

    send = commands.add_parser("send", help="Send an explicitly approved message")
    send.add_argument("--to", required=True)
    send.add_argument("--cc")
    send.add_argument("--bcc")
    send.add_argument("--subject", required=True)
    send.add_argument("--body", required=True)
    send.add_argument("--authorisation", required=True)
    send.set_defaults(function=command_send)

    trash = commands.add_parser("trash", help="Move an explicitly approved message to Trash")
    trash.add_argument("message_id")
    trash.add_argument("--authorisation", required=True)
    trash.set_defaults(function=command_trash)

    untrash = commands.add_parser("untrash", help="Restore a message from Trash")
    untrash.add_argument("message_id")
    untrash.add_argument("--authorisation", required=True)
    untrash.set_defaults(function=command_untrash)
    return value


def main() -> int:
    try:
        args = parser().parse_args()
        if getattr(args, "timeout", 1) < 1 or getattr(args, "timeout", 1) > 900:
            raise GmailError("OAuth timeout must be between 1 and 900 seconds")
        args.function(args)
    except (GmailError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
