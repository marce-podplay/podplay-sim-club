from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from podplay_sim_club.asset_discovery import (  # noqa: E402
    AssetDiscoveryError,
    discover_player_assets_with_client,
)
from podplay_sim_club.preview_readonly import PreviewReadError  # noqa: E402
from podplay_sim_club.storage import Storage  # noqa: E402


OBSERVED_AT = datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc)
TARGET = "pr-7200:origin-testfingerprint"


class FakeReadClient:
    def __init__(self, profile, payment, payment_elements, packages, failures=None):
        self.profile = profile
        self.payment = payment
        self.payment_elements = payment_elements
        self.packages = packages
        self.failures = set(failures or [])
        self.paths = []

    def get(self, path):
        self.paths.append(path)
        if path.startswith("/apis/v2/users/current?"):
            key = "profile"
            value = self.profile
        elif path == "/apis/v2/users/current/payment-method":
            key = "payment"
            value = self.payment
        elif path.startswith("/apis/v2/users/current/payment-elements?"):
            key = "payment_elements"
            value = self.payment_elements
        elif path.startswith("/apis/v2/users/current/packages?"):
            key = "packages"
            value = self.packages
        else:
            raise AssertionError(f"unexpected GET: {path}")
        if key in self.failures:
            raise PreviewReadError(f"simulated {key} failure")
        return value


def profile(actor, membership=None):
    value = {
        "id": f"private-product-id-{actor}",
        "email": f"{actor}@private.test",
        "roles": [],
    }
    if membership is not ...:
        value["membershipEnrollment"] = membership
    return value


def active_package(kind, label, remaining, expiration=None):
    return {
        "id": "private-package-id",
        "displayName": "Private source package",
        "status": "ACTIVE",
        "expirationDate": expiration,
        "items": {
            "items": [{
                "id": "private-item-id",
                "type": kind,
                "displayName": label,
                "displayNamePlural": f"{label}s",
                "totalItems": 10,
                "remainingItems": remaining,
            }],
            "_total": 1,
        },
    }


def payment_elements(ready):
    if not ready:
        return {"items": [], "_total": 0}
    return {
        "items": [{
            "id": "private-payment-id",
            "contextAvailability": {
                "items": [{"id": "booking", "isDefault": True}],
                "_total": 1,
            },
        }],
        "_total": 1,
    }


class AssetDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sim-club-assets-"))
        self.storage = Storage(self.root)

    def tearDown(self):
        shutil.rmtree(self.root)

    def discover(self, actor, client):
        return discover_player_assets_with_client(
            self.storage,
            actor_id=actor,
            expected_email=f"{actor}@private.test",
            client=client,
            target_fingerprint=TARGET,
            observed_at=OBSERVED_AT,
        )

    def test_two_players_use_same_flow_but_keep_distinct_assets(self):
        membership = {
            "id": "private-enrollment-id",
            "type": "AREA_MEMBER",
            "displayName": "W37 Member",
            "isOwner": True,
            "startDate": "2026-10-01T00:00:00Z",
            "membership": {
                "id": "private-membership-id",
                "type": "AREA_MEMBER",
                "displayName": "W37 Member",
            },
        }
        king_client = FakeReadClient(
            profile("king-captain", membership),
            {"virtualCredits": 40},
            payment_elements(True),
            {"items": [active_package("BOOKING", "Play Pass", 2)], "_total": 1},
        )
        mai_client = FakeReadClient(
            profile("mai", None),
            {"virtualCredits": 0},
            payment_elements(False),
            {"items": [], "_total": 0},
        )

        king = self.discover("king-captain", king_client)
        mai = self.discover("mai", mai_client)

        self.assertEqual(4, len(king_client.paths))
        self.assertEqual(king_client.paths, mai_client.paths)
        self.assertEqual(
            {"amount": 40.0, "unit": "virtual_credit"},
            king["subjects"]["virtual_credit_balance"]["value"],
        )
        self.assertEqual(
            {"amount": 0.0, "unit": "virtual_credit"},
            mai["subjects"]["virtual_credit_balance"]["value"],
        )
        self.assertEqual([], mai["subjects"]["owned_package_entitlements"]["value"])
        self.assertEqual(
            "booking",
            king["subjects"]["owned_package_entitlements"]["value"][0]["kind"],
        )
        self.assertFalse(mai["subjects"]["membership_assignment"]["value"]["assigned"])
        self.assertEqual(0, king["remoteWrites"])

        serialized = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (self.storage.state / "evidence").glob("*.json")
        )
        self.assertNotIn("private-product-id", serialized)
        self.assertNotIn("private-payment-id", serialized)
        self.assertNotIn("@private.test", serialized)

    def test_endpoint_failure_is_unknown_without_erasing_successes(self):
        client = FakeReadClient(
            profile("andy-bogard", None),
            {"virtualCredits": 12},
            payment_elements(False),
            {"items": []},
            failures={"packages"},
        )
        result = self.discover("andy-bogard", client)

        self.assertEqual("observed", result["subjects"]["virtual_credit_balance"]["status"])
        self.assertEqual("observed", result["subjects"]["payment_readiness"]["status"])
        packages = result["subjects"]["owned_package_entitlements"]
        self.assertEqual("unknown", packages["status"])
        self.assertEqual("error", packages["result"])
        self.assertEqual("request_failed", packages["errorCode"])

        beliefs = json.loads(
            (self.storage.state / "projections" / "beliefs.json").read_text(encoding="utf-8")
        )["beliefs"]
        self.assertEqual(
            "observed", beliefs["belief:andy-bogard:virtual_credit_balance"]["status"]
        )
        self.assertEqual(
            "unknown", beliefs["belief:andy-bogard:owned_package_entitlements"]["status"]
        )

    def test_missing_credit_is_unknown_not_zero(self):
        client = FakeReadClient(
            profile("terry-bogard", None),
            {},
            payment_elements(False),
            {"items": []},
        )
        result = self.discover("terry-bogard", client)
        credit = result["subjects"]["virtual_credit_balance"]
        self.assertEqual("unknown", credit["status"])
        self.assertEqual("missing_field", credit["errorCode"])
        self.assertIsNone(credit["value"])
        self.assertEqual("observed", result["subjects"]["payment_readiness"]["status"])
        self.assertEqual({"ready": False}, result["subjects"]["payment_readiness"]["value"])

    def test_malformed_package_does_not_poison_other_subjects(self):
        malformed = active_package("BOOKING", "Play Pass", 2)
        malformed["expirationDate"] = "not-a-time"
        client = FakeReadClient(
            profile("mai", None),
            {"virtualCredits": 1},
            payment_elements(False),
            {"items": [malformed]},
        )
        result = self.discover("mai", client)
        self.assertEqual("observed", result["subjects"]["virtual_credit_balance"]["status"])
        self.assertEqual("unknown", result["subjects"]["owned_package_entitlements"]["status"])
        self.assertEqual(
            "unexpected_shape",
            result["subjects"]["owned_package_entitlements"]["errorCode"],
        )

    def test_secret_shaped_customer_label_is_not_persisted(self):
        unsafe = active_package("BOOKING", "https://private.invalid/pass", 1)
        client = FakeReadClient(
            profile("mai", None),
            {"virtualCredits": 1},
            payment_elements(False),
            {"items": [unsafe]},
        )
        result = self.discover("mai", client)
        packages = result["subjects"]["owned_package_entitlements"]
        self.assertEqual("unknown", packages["status"])
        self.assertEqual("unexpected_shape", packages["errorCode"])
        evidence_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (self.storage.state / "evidence").glob("*.json")
        )
        self.assertNotIn("private.invalid", evidence_text)

    def test_identity_mismatch_stops_before_any_evidence_is_written(self):
        client = FakeReadClient(
            profile("other-person", None),
            {"virtualCredits": 0},
            payment_elements(False),
            {"items": []},
        )
        with self.assertRaises(AssetDiscoveryError):
            self.discover("andy-bogard", client)
        self.assertFalse((self.storage.state / "evidence").exists())
        self.assertFalse((self.storage.state / "journal" / "facts.jsonl").exists())

    def test_payment_elements_failure_does_not_hide_credit_balance(self):
        client = FakeReadClient(
            profile("mai", None),
            {"virtualCredits": 7},
            payment_elements(True),
            {"items": []},
            failures={"payment_elements"},
        )
        result = self.discover("mai", client)

        self.assertEqual(
            {"amount": 7.0, "unit": "virtual_credit"},
            result["subjects"]["virtual_credit_balance"]["value"],
        )
        readiness = result["subjects"]["payment_readiness"]
        self.assertEqual("unknown", readiness["status"])
        self.assertEqual("request_failed", readiness["errorCode"])

    def test_payment_readiness_requires_default_booking_context(self):
        not_default = {
            "items": [{
                "contextAvailability": {
                    "items": [{"id": "booking", "isDefault": False}],
                },
            }],
        }
        client = FakeReadClient(
            profile("mai", None),
            {"virtualCredits": 0},
            not_default,
            {"items": []},
        )
        result = self.discover("mai", client)
        self.assertEqual(
            {"ready": False}, result["subjects"]["payment_readiness"]["value"]
        )


if __name__ == "__main__":
    unittest.main()
