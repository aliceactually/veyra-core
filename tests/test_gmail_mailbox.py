import argparse
import base64
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch
import urllib.parse
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gmail-mailbox.py"
SPEC = importlib.util.spec_from_file_location("gmail_mailbox", SCRIPT)
gmail = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(gmail)


class FakeSession:
    def __init__(self, _config):
        self.config = {
            "account": "primary@example.test",
            "from_alias": "alias@example.test",
            "credential_id": "a" * 32,
        }
        self.calls = []

    def request(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if path == "settings/sendAs":
            return {
                "sendAs": [
                    {
                        "sendAsEmail": "alias@example.test",
                        "verificationStatus": "accepted",
                    }
                ]
            }
        if path == "messages/send":
            return {"id": "sent-id", "threadId": "thread-id"}
        if path.endswith("/trash") or path.endswith("/untrash"):
            return {"id": path.split("/")[1]}
        return {}


class GmailMailboxTests(unittest.TestCase):
    def test_desktop_client_json_and_scope(self):
        client = gmail.parse_client_json(
            json.dumps(
                {
                    "installed": {
                        "client_id": "client-id.apps.googleusercontent.com",
                        "client_secret": "client-secret",
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                    }
                }
            ).encode()
        )
        url = gmail.build_authorisation_url(
            client,
            redirect_uri="http://127.0.0.1:1234",
            state="state",
            verifier="verifier",
            account="primary@example.test",
        )
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(query["scope"], [gmail.GMAIL_SCOPE])
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["prompt"], ["consent"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["login_hint"], ["primary@example.test"])
        self.assertNotIn("client-secret", url)

    def test_rejects_non_desktop_client(self):
        with self.assertRaisesRegex(gmail.GmailError, "Desktop app"):
            gmail.parse_client_json(
                json.dumps(
                    {
                        "web": {
                            "client_id": "client-id",
                            "client_secret": "client-secret",
                        }
                    }
                ).encode()
            )

    def test_rejects_non_google_oauth_endpoints(self):
        for field, endpoint in (
            ("auth_uri", "https://attacker.example/authorize"),
            ("token_uri", "https://attacker.example/token"),
        ):
            client = {
                "client_id": "client-id.apps.googleusercontent.com",
                "client_secret": "client-secret",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
            client[field] = endpoint
            with self.assertRaisesRegex(gmail.GmailError, "non-Google"):
                gmail.parse_client_json(json.dumps({"installed": client}).encode())

    def test_extract_body_prefers_plain_text(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": gmail.b64url(b"<b>hello</b>")},
                },
                {
                    "mimeType": "text/plain",
                    "body": {"data": gmail.b64url(b"hello")},
                },
            ],
        }
        self.assertEqual(gmail.extract_body(payload), ("text/plain", "hello"))

    def test_public_aliases_exposes_no_signature_or_smtp_details(self):
        aliases = gmail.public_aliases(
            {
                "sendAs": [
                    {
                        "sendAsEmail": "alias@example.test",
                        "verificationStatus": "accepted",
                        "isPrimary": False,
                        "isDefault": True,
                        "signature": "private signature",
                        "smtpMsa": {"username": "private"},
                    }
                ]
            }
        )
        self.assertEqual(
            aliases,
            [
                {
                    "email": "alias@example.test",
                    "verification_status": "accepted",
                    "primary": False,
                    "default": True,
                }
            ],
        )

    def test_send_requires_authorisation_and_uses_alias(self):
        fake = FakeSession(None)
        args = argparse.Namespace(
            config=Path("unused"),
            to="recipient@example.com",
            cc=None,
            bcc=None,
            subject="Approved subject",
            body="Approved body",
            authorisation="Alice approved this exact message in authenticated chat",
        )
        with (
            patch.object(gmail, "GmailSession", return_value=fake),
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            gmail.command_send(args)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "sent")
        raw = fake.calls[-1][1]["data"]["raw"]
        message = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        self.assertIn("From: alias@example.test", message)
        self.assertIn("To: recipient@example.com", message)
        self.assertIn("Subject: Approved subject", message)

        args.authorisation = ""
        with self.assertRaisesRegex(gmail.GmailError, "authorisation"):
            gmail.command_send(args)

    def test_trash_is_recoverable_post_and_requires_authorisation(self):
        fake = FakeSession(None)
        args = argparse.Namespace(
            config=Path("unused"),
            message_id="message-id",
            authorisation="Alice approved moving this message to Trash",
        )
        with (
            patch.object(gmail, "GmailSession", return_value=fake),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            gmail.command_trash(args)
        self.assertEqual(
            fake.calls[-1],
            ("messages/message-id/trash", {"method": "POST", "data": {}}),
        )


if __name__ == "__main__":
    unittest.main()
