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


# ----------------------------------------------------------------------------
# add_negative_keywords (campaignCriteria:mutate)
# ----------------------------------------------------------------------------

_MATCH_TYPES = {"EXACT", "PHRASE", "BROAD"}
_GOOGLE_MAX_KEYWORDS_PER_CALL = 50


@mcp.tool()
async def add_negative_keywords(
    customer_id: Annotated[
        str,
        Field(description="Google Ads customer ID (10 digits, no dashes)."),
    ],
    campaign_id: Annotated[
        str,
        Field(description="Campaign ID that will receive the negatives."),
    ],
    keywords: Annotated[
        list[str],
        Field(description="Negative keyword texts. Duplicates (case-insensitive) and existing negatives are filtered out before the API call."),
    ],
    match_type: Annotated[
        str,
        Field(description="Match type for the negatives — EXACT, PHRASE, or BROAD. EXACT is the safe default."),
    ] = "EXACT",
    validate_only: Annotated[
        bool,
        Field(description="If True (default), Google validates but does not apply."),
    ] = True,
) -> dict:
    """Attach negative keywords to a campaign.

    Uses ``campaignCriteria:mutate`` with one ``create`` operation per
    keyword. Only restricts spending, so risk is low — this is the
    highest-value tool in the weekly audit loop.

    Workflow:

    1. Validate ``match_type`` is one of EXACT/PHRASE/BROAD.
    2. Fetch existing negatives for the campaign via GAQL and strip
       any duplicates (case-insensitive, same match type) from the
       input list. Duplicates land in ``warnings`` so the operator can
       see what was filtered.
    3. Cap the remaining list at Google's 50-per-call limit. If the
       caller sent more, the excess is dropped and reported as a
       warning (the caller can re-issue in batches).
    4. Issue the mutate with ``partialFailure=True`` so one malformed
       keyword does not kill the batch.

    Args:
        customer_id: Google Ads customer ID.
        campaign_id: Target campaign.
        keywords: Keyword texts (whitespace is trimmed; empty strings
            are ignored).
        match_type: EXACT/PHRASE/BROAD.
        validate_only: Default True.

    Returns:
        CREAVY mutate envelope. ``resource_names`` lists the created
        criterion resources (or would-be resources in validate_only
        mode if the API echoes them).
    """
    match_type_upper = (match_type or "").upper()
    if match_type_upper not in _MATCH_TYPES:
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": [
                f"invalid match_type {match_type!r} — must be one of {sorted(_MATCH_TYPES)}"
            ],
            "raw": {},
        }

    # Normalise input: trim, drop empties, dedupe case-insensitively.
    seen: set[str] = set()
    cleaned: list[str] = []
    for kw in keywords or []:
        trimmed = (kw or "").strip()
        if not trimmed:
            continue
        key = trimmed.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(trimmed)

    if not cleaned:
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": ["no non-empty keywords supplied after trimming/dedup"],
            "raw": {},
        }

    formatted_customer_id = format_customer_id(customer_id)
    campaign_resource = (
        f"customers/{formatted_customer_id}/campaigns/{campaign_id}"
    )
    client = GoogleAdsClient()

    # Fetch existing negatives so we do not double-insert.
    existing_query = (
        "SELECT campaign_criterion.keyword.text, campaign_criterion.keyword.match_type "
        "FROM campaign_criterion "
        f"WHERE campaign_criterion.negative = TRUE AND campaign.id = {campaign_id}"
    )
    existing: set[tuple[str, str]] = set()
    try:
        resp = client.search(formatted_customer_id, existing_query)
        for row in resp.get("results", []) or []:
            kw_info = row.get("campaignCriterion", {}).get("keyword", {})
            text = (kw_info.get("text") or "").lower()
            mtype = (kw_info.get("matchType") or "").upper()
            if text:
                existing.add((text, mtype))
    except Exception as exc:  # noqa: BLE001
        logger.warning("existing-negatives pre-check failed: %s", exc)
        # We still proceed — worst case, Google rejects duplicates as partial failures.

    warnings: list[str] = []
    kept: list[str] = []
    for kw in cleaned:
        if (kw.lower(), match_type_upper) in existing:
            warnings.append(f"skip duplicate negative: {kw!r}")
            continue
        kept.append(kw)

    # Enforce Google's per-call cap.
    if len(kept) > _GOOGLE_MAX_KEYWORDS_PER_CALL:
        dropped = kept[_GOOGLE_MAX_KEYWORDS_PER_CALL:]
        kept = kept[:_GOOGLE_MAX_KEYWORDS_PER_CALL]
        warnings.append(
            f"exceeded Google per-call limit of {_GOOGLE_MAX_KEYWORDS_PER_CALL}; "
            f"dropped {len(dropped)} keyword(s): re-issue in another batch"
        )

    if not kept:
        return {
            "success": True,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": warnings or ["all supplied keywords are already present — no-op"],
            "raw": {},
        }

    operations = [
        {
            "create": {
                "campaign": campaign_resource,
                "negative": True,
                "keyword": {
                    "text": kw,
                    "matchType": match_type_upper,
                },
            }
        }
        for kw in kept
    ]

    raw = client.mutate(
        customer_id=formatted_customer_id,
        resource="campaignCriteria",
        operations=operations,
        validate_only=validate_only,
    )
    envelope = _normalize_response(raw, validate_only)
    envelope["warnings"] = warnings + envelope.get("warnings", [])
    return envelope
