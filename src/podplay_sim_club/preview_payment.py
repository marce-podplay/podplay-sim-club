"""Test-only payment-method seed through PodPlay setup intents and Stripe."""

import base64
import json
from pathlib import Path
import re
import time
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .preview_readonly import MAX_RESPONSE_BYTES, PreviewReadonlyClient
from .preview_write import PreviewWriteError
from .target_policy import TargetPolicy


STRIPE_API_ORIGIN = "https://api.stripe.com"
SETUP_INTENT_ID = re.compile(r"^seti_[A-Za-z0-9_]+$")


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        raise PreviewWriteError("payment-method seed refused an HTTP redirect")


def load_test_stripe_secret(path: Path) -> str:
    if not path.is_file():
        raise PreviewWriteError("Stripe environment file does not exist")
    value = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        name, candidate = line.split("=", 1)
        if name.strip() in {"STRIPE_SECRET_KEY", "CYPRESS_STRIPE_SECRET_KEY"}:
            value = candidate.strip().strip("'\"")
            break
    if not value:
        raise PreviewWriteError("Stripe test secret is missing from the environment file")
    if not value.startswith("sk_test_"):
        raise PreviewWriteError("payment-method seed refuses non-test Stripe secrets")
    return value


class PreviewPaymentMethodWriter:
    def __init__(self, policy: TargetPolicy, actor_token: str, stripe_test_secret: str):
        if not policy.writes_allowed:
            raise PreviewWriteError("preview-write policy confirmation is required")
        if not stripe_test_secret.startswith("sk_test_"):
            raise PreviewWriteError("payment-method seed requires a Stripe test secret")
        self.policy = policy
        self._actor_token = actor_token
        self._stripe_secret = stripe_test_secret
        self._opener = build_opener(_RejectRedirects())

    def add_visa(self) -> str:
        setup_intent_id = self._create_setup_intent()
        self._confirm_setup_intent(setup_intent_id)
        return setup_intent_id

    def _create_setup_intent(self) -> str:
        url = (
            self.policy.target_origin
            + "/apis/v2/users/current/payment-method/stripe/setup-intents"
        )
        self.policy.assert_url(url)
        request = Request(
            url,
            data=b'{"context":"booking"}',
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._actor_token}",
                "User-Agent": "podplay-sim-club/0.1 payment-seed",
            },
            method="POST",
        )
        value = self._request_json(request, expected_status=201, boundary="PodPlay")
        setup_id = value.get("id")
        if not isinstance(setup_id, str) or not SETUP_INTENT_ID.fullmatch(setup_id):
            raise PreviewWriteError("PodPlay setup-intent response has an invalid ID")
        return setup_id

    def _confirm_setup_intent(self, setup_intent_id: str) -> None:
        if not SETUP_INTENT_ID.fullmatch(setup_intent_id):
            raise PreviewWriteError("Stripe setup-intent ID is invalid")
        url = f"{STRIPE_API_ORIGIN}/v1/setup_intents/{setup_intent_id}/confirm"
        authorization = base64.b64encode(
            (self._stripe_secret + ":").encode("utf-8")
        ).decode("ascii")
        request = Request(
            url,
            data=urlencode({"payment_method": "pm_card_visa"}).encode("ascii"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {authorization}",
                "User-Agent": "podplay-sim-club/0.1 payment-seed",
            },
            method="POST",
        )
        value = self._request_json(request, expected_status=200, boundary="Stripe")
        if value.get("id") != setup_intent_id or value.get("status") != "succeeded":
            raise PreviewWriteError("Stripe test setup intent did not succeed")

    def _request_json(
        self, request: Request, expected_status: int, boundary: str
    ) -> Dict[str, Any]:
        try:
            with self._opener.open(request, timeout=20) as response:
                final_url = response.geturl()
                if boundary == "PodPlay":
                    self.policy.assert_url(final_url)
                elif not final_url.startswith(STRIPE_API_ORIGIN + "/"):
                    raise PreviewWriteError("Stripe request crossed its fixed origin")
                body = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
        except PreviewWriteError:
            raise
        except HTTPError as exc:
            raise PreviewWriteError(
                f"{boundary} payment-method seed failed with HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise PreviewWriteError(
                f"{boundary} payment-method seed network request failed"
            ) from exc
        if status != expected_status:
            raise PreviewWriteError(
                f"{boundary} payment-method seed returned HTTP {status}"
            )
        if len(body) > MAX_RESPONSE_BYTES:
            raise PreviewWriteError(
                f"{boundary} payment-method seed response exceeded the size limit"
            )
        try:
            value = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PreviewWriteError(
                f"{boundary} payment-method seed returned invalid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise PreviewWriteError(
                f"{boundary} payment-method seed returned an unexpected shape"
            )
        return value


def wait_for_payment_method(client: PreviewReadonlyClient) -> bool:
    for delay in (0.5, 1, 2, 3, 3, 5, 5, 5):
        time.sleep(delay)
        payment = client.get("/apis/v2/users/current/payment-method")
        if _has_payment_method(payment):
            return True
    return False


def _has_payment_method(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    element = value.get("paymentElement")
    return bool(isinstance(element, dict) and element.get("id")) or bool(
        value.get("preferredCard") or value.get("preferredBankAccount")
    )
