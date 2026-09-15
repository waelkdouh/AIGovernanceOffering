"""Idempotent Azure API Management (APIM) control-plane helpers.

All functions use ARM REST (`https://management.azure.com`) with PUT/upsert
semantics so they are safe to call repeatedly (re-running a notebook must
never fail or duplicate resources). Every function is reusable by any of the
workshop demos, not just Demo 1.
"""

from __future__ import annotations

import json as _json
import time
from typing import Any, Dict, List, Optional

import requests

from .auth import get_arm_token

ARM_BASE = "https://management.azure.com"
API_VERSION = "2022-08-01"

# How long to wait for APIM's async provisioning (e.g. a service that is
# still "Updating") before giving up.
_POLL_TIMEOUT_SECONDS = 300
_POLL_INTERVAL_SECONDS = 5


class ApimError(RuntimeError):
    """Raised when an ARM call against APIM fails, with full context."""

    def __init__(self, method: str, url: str, response: requests.Response):
        self.method = method
        self.url = url
        self.status_code = response.status_code
        self.body = response.text
        super().__init__(
            f"{method} {url} failed with {response.status_code}: {response.text}"
        )


def _headers() -> Dict[str, str]:
    token = get_arm_token()
    return {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    }


def _service_scope(subscription_id: str, resource_group: str, apim_name: str) -> str:
    return (
        f"{ARM_BASE}/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.ApiManagement/service/{apim_name}"
    )


def _request(
    method: str,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    ok_statuses: Optional[List[int]] = None,
) -> requests.Response:
    params = dict(params or {})
    params.setdefault("api-version", API_VERSION)
    response = requests.request(
        method, url, headers=_headers(), params=params, json=json_body, timeout=60
    )
    ok_statuses = ok_statuses or [200, 201, 202, 204]
    if response.status_code == 404 and method == "GET":
        return response
    if response.status_code not in ok_statuses:
        raise ApimError(method, response.url, response)
    return response


def _json_body(response: requests.Response) -> Dict[str, Any]:
    """Parse an ARM response body, tolerating a UTF-8 BOM and empty payloads."""
    if not response.content:
        return {}
    text = response.text.lstrip("\ufeff")
    if not text.strip():
        return {}
    try:
        return _json.loads(text)
    except ValueError:
        return {}


def _wait_for_completion(response: requests.Response) -> None:
    """Poll an ARM async operation (202 Accepted) until it finishes."""
    if response.status_code != 202:
        return
    location = response.headers.get("Location") or response.headers.get("Azure-AsyncOperation")
    if not location:
        return
    deadline = time.time() + _POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        poll = requests.get(location, headers=_headers(), timeout=60)
        if poll.status_code in (200, 201, 204):
            body = _json_body(poll)
            status = (body or {}).get("status")
            if status in (None, "Succeeded"):
                return
            if status in ("Failed", "Canceled"):
                raise ApimError("GET", location, poll)
        time.sleep(_POLL_INTERVAL_SECONDS)


def ensure_api(
    subscription_id: str,
    resource_group: str,
    apim_name: str,
    api_id: str,
    display_name: str,
    path: str,
    service_url: Optional[str] = None,
    protocols: Optional[List[str]] = None,
    subscription_required: bool = True,
) -> Dict[str, Any]:
    """Create or update (idempotent) an API on the APIM instance."""
    url = f"{_service_scope(subscription_id, resource_group, apim_name)}/apis/{api_id}"
    body = {
        "properties": {
            "displayName": display_name,
            "path": path,
            "protocols": protocols or ["https"],
            "subscriptionRequired": subscription_required,
        }
    }
    if service_url:
        body["properties"]["serviceUrl"] = service_url
    response = _request("PUT", url, json_body=body)
    _wait_for_completion(response)
    return _json_body(response)


def ensure_operation(
    subscription_id: str,
    resource_group: str,
    apim_name: str,
    api_id: str,
    operation_id: str,
    display_name: str,
    method: str,
    url_template: str,
) -> Dict[str, Any]:
    """Create or update (idempotent) an operation on an API."""
    url = (
        f"{_service_scope(subscription_id, resource_group, apim_name)}"
        f"/apis/{api_id}/operations/{operation_id}"
    )
    body = {
        "properties": {
            "displayName": display_name,
            "method": method,
            "urlTemplate": url_template,
        }
    }
    response = _request("PUT", url, json_body=body)
    _wait_for_completion(response)
    return _json_body(response)


def ensure_backend(
    subscription_id: str,
    resource_group: str,
    apim_name: str,
    backend_id: str,
    backend_url: str,
    description: str = "",
    protocol: str = "http",
) -> Dict[str, Any]:
    """Create or update (idempotent) a backend pointing at the AOAI endpoint.

    APIM backend ``protocol`` is the backend style enum (``"http"`` versus
    ``"soap"``), not the transport scheme. Keep HTTPS backends as
    ``protocol="http"``; TLS transport is selected by the ``https://`` scheme
    in ``backend_url``. APIM's REST contract does not accept ``"https"`` here.
    Public Azure endpoints should keep both TLS certificate validation flags
    enabled.
    """
    url = f"{_service_scope(subscription_id, resource_group, apim_name)}/backends/{backend_id}"
    body = {
        "properties": {
            "url": backend_url.rstrip("/"),
            "protocol": protocol,
            "description": description or backend_id,
            "tls": {"validateCertificateChain": True, "validateCertificateName": True},
        }
    }
    response = _request("PUT", url, json_body=body)
    _wait_for_completion(response)
    return _json_body(response)


def ensure_named_value(
    subscription_id: str,
    resource_group: str,
    apim_name: str,
    named_value_id: str,
    display_name: str,
    value: str,
    secret: bool = True,
) -> Dict[str, Any]:
    """Create or update (idempotent) a named value (e.g. the AOAI key).

    Convention: the named value id and display name must be identical and
    lowercase, matching the ``{{token}}`` used in the policy XML. APIM resolves
    policy ``{{...}}`` references against the named value *display name*, and
    the lookup is case-sensitive -- a mismatch surfaces later as an opaque
    policy validation error (HTTP 400, "Cannot find a property '<token>'").
    """
    if display_name != named_value_id:
        raise ValueError(
            f"Named value display_name {display_name!r} must match id "
            f"{named_value_id!r}; policy '{{{{...}}}}' lookups are by display "
            "name and are case-sensitive."
        )
    url = (
        f"{_service_scope(subscription_id, resource_group, apim_name)}"
        f"/namedValues/{named_value_id}"
    )
    body = {
        "properties": {
            "displayName": display_name,
            "value": value,
            "secret": secret,
        }
    }
    response = _request("PUT", url, json_body=body)
    _wait_for_completion(response)
    return _json_body(response)


def ensure_product(
    subscription_id: str,
    resource_group: str,
    apim_name: str,
    product_id: str,
    display_name: str,
    description: str = "",
    subscription_required: bool = True,
    state: str = "published",
) -> Dict[str, Any]:
    """Create or update (idempotent) a product used to scope a subscription."""
    url = f"{_service_scope(subscription_id, resource_group, apim_name)}/products/{product_id}"
    body = {
        "properties": {
            "displayName": display_name,
            "description": description or display_name,
            "subscriptionRequired": subscription_required,
            "state": state,
        }
    }
    response = _request("PUT", url, json_body=body)
    _wait_for_completion(response)
    return _json_body(response)


def ensure_product_api_link(
    subscription_id: str,
    resource_group: str,
    apim_name: str,
    product_id: str,
    api_id: str,
) -> None:
    """Link an API to a product (idempotent).

    Uses the classic product-API association endpoint
    (`/products/{productId}/apis/{apiId}`), which is supported by the
    `API_VERSION` pinned above. The newer `apiLinks` collection only exists in
    2023-03-01-preview and later; calling it with an older api-version makes
    ARM reject the URL with a generic `ResourceNotFound` / "request did not
    have proper uri path format" error.

    The PUT takes no request body and returns 201 on first link, 200 if the
    API is already associated with the product.
    """
    url = (
        f"{_service_scope(subscription_id, resource_group, apim_name)}"
        f"/products/{product_id}/apis/{api_id}"
    )
    _request("PUT", url, ok_statuses=[200, 201, 204])


def ensure_subscription(
    subscription_id: str,
    resource_group: str,
    apim_name: str,
    apim_subscription_id: str,
    display_name: str,
    scope: str,
) -> Dict[str, Any]:
    """Create or update (idempotent) an APIM subscription scoped to a product/API.

    `scope` should look like `/products/{product_id}` or `/apis/{api_id}`.
    """
    url = (
        f"{_service_scope(subscription_id, resource_group, apim_name)}"
        f"/subscriptions/{apim_subscription_id}"
    )
    body = {
        "properties": {
            "displayName": display_name,
            "scope": scope,
            "state": "active",
        }
    }
    response = _request("PUT", url, json_body=body)
    _wait_for_completion(response)
    return _json_body(response)


def set_api_policy(
    subscription_id: str,
    resource_group: str,
    apim_name: str,
    api_id: str,
    policy_xml: str,
) -> Dict[str, Any]:
    """Apply (idempotent, PUT) an XML policy document at API scope."""
    url = (
        f"{_service_scope(subscription_id, resource_group, apim_name)}"
        f"/apis/{api_id}/policies/policy"
    )
    body = {"properties": {"format": "xml", "value": policy_xml}}
    response = _request("PUT", url, json_body=body)
    _wait_for_completion(response)
    return _json_body(response)


def get_api_policy(
    subscription_id: str,
    resource_group: str,
    apim_name: str,
    api_id: str,
    policy_format: str = "rawxml",
) -> Dict[str, Any]:
    """Fetch the XML policy document applied at API scope.

    APIM defaults to ``format=xaml``, which returns the policy with attribute
    quotes and expressions XML-escaped (e.g. ``backend-id=&quot;...&quot;``).
    Request ``rawxml`` so callers can match directives such as
    ``set-backend-service`` literally instead of against escaped XML.
    """
    url = (
        f"{_service_scope(subscription_id, resource_group, apim_name)}"
        f"/apis/{api_id}/policies/policy"
    )
    response = _request("GET", url, params={"format": policy_format})
    if response.status_code == 404:
        raise ApimError("GET", response.url, response)
    return _json_body(response)


def get_gateway_url(subscription_id: str, resource_group: str, apim_name: str) -> str:
    """Look up the APIM instance's public gateway URL."""
    url = _service_scope(subscription_id, resource_group, apim_name)
    response = _request("GET", url)
    data = _json_body(response)
    try:
        return data["properties"]["gatewayUrl"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(
            f"GET {response.url} returned no APIM gateway URL"
        ) from exc


def get_subscription_key(
    subscription_id: str,
    resource_group: str,
    apim_name: str,
    apim_subscription_id: str,
) -> str:
    """Fetch the primary subscription key via ARM `listSecrets` (no user prompt)."""
    url = (
        f"{_service_scope(subscription_id, resource_group, apim_name)}"
        f"/subscriptions/{apim_subscription_id}/listSecrets"
    )
    response = _request("POST", url, ok_statuses=[200])
    data = _json_body(response)
    try:
        return data["primaryKey"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(
            f"POST {response.url} returned no primary subscription key"
        ) from exc


def delete_api_if_exists(
    subscription_id: str,
    resource_group: str,
    apim_name: str,
    api_id: str,
) -> bool:
    """Delete an API if it exists; tolerates 404 so cleanup is idempotent."""
    url = (
        f"{_service_scope(subscription_id, resource_group, apim_name)}/apis/{api_id}"
    )
    response = requests.delete(
        url,
        headers=_headers(),
        params={"api-version": API_VERSION, "deleteRevisions": "true"},
        timeout=60,
    )
    if response.status_code in (200, 202, 204, 404):
        _wait_for_completion(response)
        return response.status_code != 404
    raise ApimError("DELETE", response.url, response)


def delete_subscription_if_exists(
    subscription_id: str,
    resource_group: str,
    apim_name: str,
    apim_subscription_id: str,
) -> bool:
    """Delete an APIM subscription if it exists; tolerates 404."""
    url = (
        f"{_service_scope(subscription_id, resource_group, apim_name)}"
        f"/subscriptions/{apim_subscription_id}"
    )
    response = requests.delete(
        url, headers=_headers(), params={"api-version": API_VERSION}, timeout=60
    )
    if response.status_code in (200, 202, 204, 404):
        return response.status_code != 404
    raise ApimError("DELETE", response.url, response)


def delete_named_value_if_exists(
    subscription_id: str,
    resource_group: str,
    apim_name: str,
    named_value_id: str,
) -> bool:
    """Delete a named value if it exists; tolerates 404."""
    url = (
        f"{_service_scope(subscription_id, resource_group, apim_name)}"
        f"/namedValues/{named_value_id}"
    )
    response = requests.delete(
        url, headers=_headers(), params={"api-version": API_VERSION}, timeout=60
    )
    if response.status_code in (200, 202, 204, 404):
        return response.status_code != 404
    raise ApimError("DELETE", response.url, response)


def get_service(subscription_id: str, resource_group: str, apim_name: str) -> Dict[str, Any]:
    """Fetch the APIM service resource (used for reachability validation)."""
    url = _service_scope(subscription_id, resource_group, apim_name)
    response = _request("GET", url)
    if response.status_code == 404:
        raise ApimError("GET", response.url, response)
    data = _json_body(response)
    if not data:
        raise RuntimeError(f"GET {response.url} returned an empty or invalid JSON body")
    return data
