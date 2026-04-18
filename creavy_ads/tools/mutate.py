"""Write/mutate tools for Google Ads.

Every tool in this module defaults to ``validate_only=True``. A caller
flips to ``validate_only=False`` only when the user has explicitly
authorised the write in chat. The ``CREAVY_ADS_VALIDATE_ONLY`` env var
is NOT consulted here — tool defaults are the only source of truth so
behaviour is reproducible across deployments.

All mutate tools return the normalised envelope produced by
``_normalize_response``:

    {
        "success": bool,
        "validate_only": bool,
        "resource_names": list[str],
        "partial_failures": list[dict],
        "warnings": list[str],
        "raw": dict,            # unmodified API response
    }

Parameter annotations use ``Annotated[T, Field(description=...)] = default``
so that the MCP schema keeps its descriptions while the bare Python
function retains a real default value (unit tests rely on this).
"""

import logging
from typing import Annotated, Any

from pydantic import Field

from creavy_ads.auth import format_customer_id
from creavy_ads.client import GoogleAdsClient
from creavy_ads.server import mcp

logger = logging.getLogger('google_ads_server')


def _normalize_response(raw: dict, validate_only: bool) -> dict:
    """Normalise a Google Ads mutate response into the CREAVY envelope.

    Handles both the "HTTP error" shape produced by
    ``GoogleAdsClient.mutate`` on non-2xx responses (which contains
    ``error``/``status_code`` keys) and the successful response shape
    (which contains ``results`` and optionally ``partialFailureError``).
    """
    if "error" in raw and "status_code" in raw:
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": [f"HTTP {raw.get('status_code')}: {raw.get('error')}"],
            "raw": raw,
        }

    results = raw.get("results", []) or []
    resource_names = [
        r.get("resourceName") for r in results if r.get("resourceName")
    ]

    partial_failures: list[dict[str, Any]] = []
    partial = raw.get("partialFailureError") or {}
    for detail in partial.get("details", []) or []:
        for err in detail.get("errors", []) or []:
            partial_failures.append({
                "code": err.get("errorCode", {}),
                "message": err.get("message", ""),
                "location": err.get("location", {}),
            })

    return {
        "success": True,
        "validate_only": validate_only,
        "resource_names": resource_names,
        "partial_failures": partial_failures,
        "warnings": [],
        "raw": raw,
    }


@mcp.tool()
async def pause_campaign(
    customer_id: Annotated[
        str,
        Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'."),
    ],
    campaign_id: Annotated[
        str,
        Field(description="Campaign ID to pause (numeric string)."),
    ],
    validate_only: Annotated[
        bool,
        Field(
            description=(
                "If True (default), Google validates the change but does NOT apply it. "
                "Set to False only after the user has explicitly confirmed the apply."
            ),
        ),
    ] = True,
) -> dict:
    """Pause a Google Ads campaign (transitions status to ``PAUSED``).

    Reversible via ``enable_campaign``. The campaign keeps its budget,
    ads, keywords and history — it simply stops serving impressions.

    Read-before-write: we fetch the campaign's current status via GAQL
    before issuing the mutate. If it is already ``PAUSED``, we return a
    no-op envelope (``warnings`` mentions the no-op) and do not hit the
    mutate endpoint.

    Args:
        customer_id: Google Ads customer ID.
        campaign_id: Numeric campaign ID to pause.
        validate_only: If True (default), Google validates but does not
            apply. Use False only on explicit user confirmation.

    Returns:
        The CREAVY mutate envelope (see module docstring).
    """
    formatted_customer_id = format_customer_id(customer_id)
    campaign_resource = (
        f"customers/{formatted_customer_id}/campaigns/{campaign_id}"
    )

    client = GoogleAdsClient()

    # Read-before-write: confirm the campaign exists and get current status.
    pre_query = (
        "SELECT campaign.id, campaign.name, campaign.status "
        "FROM campaign "
        f"WHERE campaign.id = {campaign_id}"
    )
    try:
        pre = client.search(formatted_customer_id, pre_query)
    except Exception as exc:  # noqa: BLE001 - surfaced as a warning
        logger.warning("pause_campaign pre-check failed: %s", exc)
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": [f"pre-check failed: {exc}"],
            "raw": {},
        }

    rows = pre.get("results", []) or []
    if not rows:
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": [
                f"campaign {campaign_id} not found in account {formatted_customer_id}"
            ],
            "raw": pre,
        }

    current_status = rows[0].get("campaign", {}).get("status", "UNKNOWN")
    if current_status == "PAUSED":
        return {
            "success": True,
            "validate_only": validate_only,
            "resource_names": [campaign_resource],
            "partial_failures": [],
            "warnings": [
                f"campaign {campaign_id} is already PAUSED — no-op, mutate not called"
            ],
            "raw": pre,
        }

    operation = {
        "update": {
            "resourceName": campaign_resource,
            "status": "PAUSED",
        },
        "updateMask": "status",
    }
    raw = client.mutate(
        customer_id=formatted_customer_id,
        resource="campaigns",
        operations=[operation],
        validate_only=validate_only,
    )
    return _normalize_response(raw, validate_only)


# ----------------------------------------------------------------------------
# enable_campaign + spend-cap safety helper
# ----------------------------------------------------------------------------

def _verify_spend_cap(client: GoogleAdsClient, formatted_customer_id: str) -> tuple[bool, str]:
    """Return (has_cap, detail) for the given account.

    Policy: we only allow ENABLE flows if Google reports an account-level
    spend cap via ``account_budget``. The API does not expose a single
    boolean, so we look for any active ``account_budget`` row.

    The check is best-effort: if the GAQL fails (e.g. missing scope,
    not a billing-managed MCC), we return ``(False, <error>)`` so the
    caller refuses the apply and surfaces the reason. This is the safe
    default — writes stay off until humans verify the cap in the UI.
    """
    query = (
        "SELECT account_budget.status, account_budget.approved_spending_limit_micros "
        "FROM account_budget "
        "WHERE account_budget.status = 'APPROVED'"
    )
    try:
        resp = client.search(formatted_customer_id, query)
    except Exception as exc:  # noqa: BLE001
        return False, f"spend-cap check failed: {exc}"
    rows = resp.get("results", []) or []
    if not rows:
        return False, "no APPROVED account_budget found — set a spend cap in Google Ads UI"
    return True, f"{len(rows)} active account_budget row(s)"


@mcp.tool()
async def enable_campaign(
    customer_id: Annotated[
        str,
        Field(description="Google Ads customer ID (10 digits, no dashes)."),
    ],
    campaign_id: Annotated[
        str,
        Field(description="Campaign ID to enable (numeric string)."),
    ],
    validate_only: Annotated[
        bool,
        Field(
            description=(
                "If True (default), Google validates the change but does NOT apply it. "
                "Set to False only after the user has explicitly confirmed the apply."
            ),
        ),
    ] = True,
) -> dict:
    """Enable a Google Ads campaign (transitions status to ``ENABLED``).

    STARTS SPENDING MONEY. Safety gates:

    1. Read-before-write: fetch current status. If already ENABLED,
       short-circuit to a no-op envelope.
    2. Spend-cap check: call ``_verify_spend_cap``. If no approved
       account_budget is found, REFUSE even with ``validate_only=False``.
       This matches the policy in ``mutate-api-design.md`` — a spend
       cap must exist in the UI before any write access is opened.

    Args:
        customer_id: Google Ads customer ID.
        campaign_id: Numeric campaign ID to enable.
        validate_only: If True (default), Google validates but does not
            apply.

    Returns:
        The CREAVY mutate envelope.
    """
    formatted_customer_id = format_customer_id(customer_id)
    campaign_resource = (
        f"customers/{formatted_customer_id}/campaigns/{campaign_id}"
    )

    client = GoogleAdsClient()

    # Safety gate 1: spend cap must exist (applies even in validate_only mode;
    # if no cap is configured, there is nothing to validate against and we
    # do not want operators thinking "validate passed => safe to apply").
    has_cap, cap_detail = _verify_spend_cap(client, formatted_customer_id)
    if not has_cap:
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": [f"refused: {cap_detail}"],
            "raw": {},
        }

    # Safety gate 2: confirm campaign exists and get current status.
    pre_query = (
        "SELECT campaign.id, campaign.name, campaign.status "
        "FROM campaign "
        f"WHERE campaign.id = {campaign_id}"
    )
    try:
        pre = client.search(formatted_customer_id, pre_query)
    except Exception as exc:  # noqa: BLE001
        logger.warning("enable_campaign pre-check failed: %s", exc)
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": [f"pre-check failed: {exc}"],
            "raw": {},
        }

    rows = pre.get("results", []) or []
    if not rows:
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": [
                f"campaign {campaign_id} not found in account {formatted_customer_id}"
            ],
            "raw": pre,
        }

    current_status = rows[0].get("campaign", {}).get("status", "UNKNOWN")
    if current_status == "ENABLED":
        return {
            "success": True,
            "validate_only": validate_only,
            "resource_names": [campaign_resource],
            "partial_failures": [],
            "warnings": [
                f"campaign {campaign_id} is already ENABLED — no-op, mutate not called"
            ],
            "raw": pre,
        }

    operation = {
        "update": {
            "resourceName": campaign_resource,
            "status": "ENABLED",
        },
        "updateMask": "status",
    }
    raw = client.mutate(
        customer_id=formatted_customer_id,
        resource="campaigns",
        operations=[operation],
        validate_only=validate_only,
    )
    envelope = _normalize_response(raw, validate_only)
    envelope.setdefault("warnings", []).append(f"spend-cap ok: {cap_detail}")
    return envelope
