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


# ----------------------------------------------------------------------------
# update_campaign_budget (campaignBudgets:mutate)
# ----------------------------------------------------------------------------

_BUDGET_CHANGE_REFUSE_THRESHOLD = 0.5  # 50% delta in either direction


@mcp.tool()
async def update_campaign_budget(
    customer_id: Annotated[
        str,
        Field(description="Google Ads customer ID (10 digits, no dashes)."),
    ],
    campaign_budget_id: Annotated[
        str,
        Field(description="campaignBudget ID (not campaign ID)."),
    ],
    amount_micros: Annotated[
        int,
        Field(description="New daily budget in account currency micros. 1 currency unit = 1_000_000 micros (e.g. 50_000_000 = 50 UAH/day)."),
    ],
    force: Annotated[
        bool,
        Field(description="Bypass the +/-50% safety check. Only set True after operator reconfirmation."),
    ] = False,
    validate_only: Annotated[
        bool,
        Field(description="If True (default), Google validates but does not apply."),
    ] = True,
) -> dict:
    """Change a campaignBudget''s daily ``amountMicros``.

    Uses ``campaignBudgets:mutate`` with ``updateMask=amount_micros``.

    Safety guardrail: before issuing the mutate we GAQL-fetch the
    current ``campaign_budget.amount_micros``. If the new value is
    more than +/-50% away from the current value, the tool refuses
    with a clear warning UNLESS ``force=True`` is passed. Both the
    before and after values always land in ``warnings`` so change
    logs are auditable.

    Args:
        customer_id: Google Ads customer ID.
        campaign_budget_id: campaignBudget ID (surface it via a GAQL
            ``SELECT campaign_budget.id FROM campaign`` pivot first if
            you only know the campaign ID).
        amount_micros: New daily budget in micros.
        force: Bypass the +/-50% guard.
        validate_only: Default True.

    Returns:
        CREAVY mutate envelope.
    """
    if amount_micros is None or amount_micros <= 0:
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": ["amount_micros must be a positive integer"],
            "raw": {},
        }

    formatted_customer_id = format_customer_id(customer_id)
    budget_resource = (
        f"customers/{formatted_customer_id}/campaignBudgets/{campaign_budget_id}"
    )
    client = GoogleAdsClient()

    pre_query = (
        "SELECT campaign_budget.id, campaign_budget.amount_micros, campaign_budget.name "
        "FROM campaign_budget "
        f"WHERE campaign_budget.id = {campaign_budget_id}"
    )
    try:
        pre = client.search(formatted_customer_id, pre_query)
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_campaign_budget pre-check failed: %s", exc)
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
                f"campaign_budget {campaign_budget_id} not found in account {formatted_customer_id}"
            ],
            "raw": pre,
        }

    current_raw = rows[0].get("campaignBudget", {}).get("amountMicros", 0)
    try:
        current = int(current_raw)
    except (TypeError, ValueError):
        current = 0

    audit_line = (
        f"budget change: before={current} micros, after={amount_micros} micros"
    )

    if current > 0:
        ratio = amount_micros / current
        delta = ratio - 1.0
        if (abs(delta) > _BUDGET_CHANGE_REFUSE_THRESHOLD) and not force:
            return {
                "success": False,
                "validate_only": validate_only,
                "resource_names": [],
                "partial_failures": [],
                "warnings": [
                    f"refused: delta={delta*100:.1f}% exceeds +/-{int(_BUDGET_CHANGE_REFUSE_THRESHOLD*100)}%; "
                    "re-issue with force=True to override",
                    audit_line,
                ],
                "raw": pre,
            }

    operation = {
        "update": {
            "resourceName": budget_resource,
            "amountMicros": str(amount_micros),
        },
        "updateMask": "amount_micros",
    }
    raw = client.mutate(
        customer_id=formatted_customer_id,
        resource="campaignBudgets",
        operations=[operation],
        validate_only=validate_only,
    )
    envelope = _normalize_response(raw, validate_only)
    envelope.setdefault("warnings", []).insert(0, audit_line)
    return envelope


# ----------------------------------------------------------------------------
# update_campaign_bid (campaigns:mutate, dynamic updateMask)
# ----------------------------------------------------------------------------

_AUTO_STRATEGIES = {"MAXIMIZE_CONVERSIONS", "TARGET_CPA"}
_ALLOWED_STRATEGIES = {"MANUAL_CPC"} | _AUTO_STRATEGIES
_MIN_CONVERSIONS_FOR_AUTO = 30


def _count_conversions_last_30d(
    client: GoogleAdsClient,
    formatted_customer_id: str,
    campaign_id: str,
) -> tuple[int, str]:
    """Return (conversions, detail) for the campaign over the last 30 days."""
    query = (
        "SELECT metrics.conversions "
        "FROM campaign "
        f"WHERE campaign.id = {campaign_id} "
        "AND segments.date DURING LAST_30_DAYS"
    )
    try:
        resp = client.search(formatted_customer_id, query)
    except Exception as exc:  # noqa: BLE001
        return 0, f"conversions GAQL failed: {exc}"
    total = 0.0
    for row in resp.get("results", []) or []:
        total += float(row.get("metrics", {}).get("conversions", 0) or 0)
    return int(total), f"{total:.2f} conversions in last 30 days"


@mcp.tool()
async def update_campaign_bid(
    customer_id: Annotated[
        str,
        Field(description="Google Ads customer ID (10 digits, no dashes)."),
    ],
    campaign_id: Annotated[
        str,
        Field(description="Campaign ID to update."),
    ],
    bid_strategy: Annotated[
        str,
        Field(description="MANUAL_CPC | MAXIMIZE_CONVERSIONS | TARGET_CPA. Phase 1 only."),
    ],
    cpc_bid_ceiling_micros: Annotated[
        int,
        Field(description="Required for MANUAL_CPC. In micros. Example: 400_000 = 0.40 currency units."),
    ] = 0,
    target_cpa_micros: Annotated[
        int,
        Field(description="Required for TARGET_CPA. In micros."),
    ] = 0,
    validate_only: Annotated[
        bool,
        Field(description="If True (default), Google validates but does not apply."),
    ] = True,
) -> dict:
    """Switch a campaign''s bidding strategy (and any required bid value).

    Uses ``campaigns:mutate`` with a dynamic ``updateMask`` covering
    only the fields actually being changed. Phase 1 supports three
    strategies: ``MANUAL_CPC``, ``MAXIMIZE_CONVERSIONS``, ``TARGET_CPA``.
    Other strategies (``MAXIMIZE_CLICKS``, ``TARGET_ROAS``, portfolio
    strategies) are intentionally left out and must be edited in the
    UI until we have a real reason to automate them.

    Safety guardrail: switching INTO an automated strategy
    (``MAXIMIZE_CONVERSIONS`` or ``TARGET_CPA``) requires at least
    ``_MIN_CONVERSIONS_FOR_AUTO`` (30) conversions in the last 30
    days, verified via GAQL. Google''s smart bidding needs that
    signal volume; starting smart bidding on a dry campaign burns
    budget without learning anything.

    Args:
        customer_id, campaign_id: resource coordinates.
        bid_strategy: one of MANUAL_CPC / MAXIMIZE_CONVERSIONS /
            TARGET_CPA.
        cpc_bid_ceiling_micros: required for MANUAL_CPC. Ignored
            for other strategies.
        target_cpa_micros: required for TARGET_CPA. Ignored for
            other strategies.
        validate_only: default True.

    Returns:
        CREAVY mutate envelope.
    """
    strategy = (bid_strategy or "").upper()
    if strategy not in _ALLOWED_STRATEGIES:
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": [
                f"bid_strategy must be one of {sorted(_ALLOWED_STRATEGIES)}, got {bid_strategy!r}"
            ],
            "raw": {},
        }

    if strategy == "MANUAL_CPC" and cpc_bid_ceiling_micros <= 0:
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": ["MANUAL_CPC requires cpc_bid_ceiling_micros > 0"],
            "raw": {},
        }
    if strategy == "TARGET_CPA" and target_cpa_micros <= 0:
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": ["TARGET_CPA requires target_cpa_micros > 0"],
            "raw": {},
        }

    formatted_customer_id = format_customer_id(customer_id)
    campaign_resource = (
        f"customers/{formatted_customer_id}/campaigns/{campaign_id}"
    )
    client = GoogleAdsClient()

    # Guardrail for auto strategies: require recent conversion volume.
    warnings: list[str] = []
    if strategy in _AUTO_STRATEGIES:
        count, detail = _count_conversions_last_30d(
            client, formatted_customer_id, campaign_id,
        )
        if count < _MIN_CONVERSIONS_FOR_AUTO:
            return {
                "success": False,
                "validate_only": validate_only,
                "resource_names": [],
                "partial_failures": [],
                "warnings": [
                    f"refused: switching to {strategy} requires >= "
                    f"{_MIN_CONVERSIONS_FOR_AUTO} conversions in the last 30 days ({detail})"
                ],
                "raw": {},
            }
        warnings.append(f"conversions check ok: {detail}")

    update_body: dict[str, Any] = {
        "resourceName": campaign_resource,
    }
    update_mask_fields: list[str] = []

    if strategy == "MANUAL_CPC":
        update_body["manualCpc"] = {}
        update_body["campaignBiddingStrategyOneof"] = "manualCpc"
        update_body["manualCpc"] = {"enhancedCpcEnabled": False}
        update_mask_fields.append("manual_cpc.enhanced_cpc_enabled")
        update_body["biddingStrategyType"] = "MANUAL_CPC"
        # Ceiling is stored on the campaign itself.
        update_body["bidCeilingMicros"] = str(cpc_bid_ceiling_micros)
        update_mask_fields.append("bid_ceiling_micros")
    elif strategy == "MAXIMIZE_CONVERSIONS":
        update_body["maximizeConversions"] = {}
        update_mask_fields.append("maximize_conversions")
    elif strategy == "TARGET_CPA":
        update_body["targetCpa"] = {"targetCpaMicros": str(target_cpa_micros)}
        update_mask_fields.append("target_cpa.target_cpa_micros")

    operation = {
        "update": update_body,
        "updateMask": ",".join(update_mask_fields),
    }
    raw = client.mutate(
        customer_id=formatted_customer_id,
        resource="campaigns",
        operations=[operation],
        validate_only=validate_only,
    )
    envelope = _normalize_response(raw, validate_only)
    envelope["warnings"] = warnings + envelope.get("warnings", [])
    return envelope


# ----------------------------------------------------------------------------
# create_ad_group (adGroups:mutate)
# ----------------------------------------------------------------------------

_AD_GROUP_STATUSES = {"PAUSED", "ENABLED"}


@mcp.tool()
async def create_ad_group(
    customer_id: Annotated[
        str,
        Field(description="Google Ads customer ID (10 digits, no dashes)."),
    ],
    campaign_id: Annotated[
        str,
        Field(description="Parent campaign ID."),
    ],
    name: Annotated[
        str,
        Field(description="Ad group name. Must be unique within the campaign."),
    ],
    cpc_bid_micros: Annotated[
        int,
        Field(description="Default CPC bid in micros. Example: 400_000 = 0.40 currency units."),
    ],
    status: Annotated[
        str,
        Field(description="PAUSED (default) or ENABLED. Default PAUSED so no ads run until the operator reviews."),
    ] = "PAUSED",
    validate_only: Annotated[
        bool,
        Field(description="If True (default), Google validates but does not apply."),
    ] = True,
) -> dict:
    """Create a SEARCH_STANDARD ad group inside an existing campaign.

    Uses ``adGroups:mutate``. An ad group is only a container — it
    does not spend money on its own, so the risk is low. The default
    ``status=PAUSED`` makes the two-step create-review-enable flow
    explicit: the caller adds ads/keywords after this returns, then
    the operator enables the group from the UI (or via a later
    ``update_ad_group`` tool).

    Args:
        customer_id, campaign_id: parent coordinates.
        name: ad group name (trimmed; must be non-empty after trim).
        cpc_bid_micros: default CPC bid (> 0).
        status: PAUSED (default) or ENABLED.
        validate_only: default True.

    Returns:
        CREAVY mutate envelope. ``resource_names`` contains the new
        ad group path (empty in pure validate_only mode because
        Google returns no resource_name for validation).
    """
    trimmed_name = (name or "").strip()
    if not trimmed_name:
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": ["name is required and must be non-empty after trimming"],
            "raw": {},
        }
    if cpc_bid_micros is None or cpc_bid_micros <= 0:
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": ["cpc_bid_micros must be a positive integer"],
            "raw": {},
        }
    status_upper = (status or "PAUSED").upper()
    if status_upper not in _AD_GROUP_STATUSES:
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": [f"status must be one of {sorted(_AD_GROUP_STATUSES)}, got {status!r}"],
            "raw": {},
        }

    formatted_customer_id = format_customer_id(customer_id)
    campaign_resource = (
        f"customers/{formatted_customer_id}/campaigns/{campaign_id}"
    )
    client = GoogleAdsClient()

    operation = {
        "create": {
            "name": trimmed_name,
            "campaign": campaign_resource,
            "status": status_upper,
            "type": "SEARCH_STANDARD",
            "cpcBidMicros": str(cpc_bid_micros),
        }
    }
    raw = client.mutate(
        customer_id=formatted_customer_id,
        resource="adGroups",
        operations=[operation],
        validate_only=validate_only,
    )
    return _normalize_response(raw, validate_only)


# ----------------------------------------------------------------------------
# create_responsive_search_ad (adGroupAds:mutate)
# ----------------------------------------------------------------------------

_HEADLINE_MIN = 3
_HEADLINE_MAX = 15
_HEADLINE_CHAR_LIMIT = 30
_DESCRIPTION_MIN = 2
_DESCRIPTION_MAX = 4
_DESCRIPTION_CHAR_LIMIT = 90
_PATH_CHAR_LIMIT = 15
_VALID_PINNED_FIELDS = {
    "HEADLINE_1", "HEADLINE_2", "HEADLINE_3",
    "DESCRIPTION_1", "DESCRIPTION_2",
}


def _is_latin_path(s: str) -> bool:
    """Path1/path2 must be ASCII letters/digits/hyphen only."""
    if not s:
        return True
    return all(c.isascii() and (c.isalnum() or c in {"-", "_"}) for c in s)


def _validate_rsa_inputs(
    headlines: list,
    descriptions: list,
    final_urls: list,
    path1: str | None,
    path2: str | None,
) -> list[str]:
    """Return a list of human-readable validation errors (empty = ok)."""
    errors: list[str] = []

    if not final_urls or not any((u or "").strip() for u in final_urls):
        errors.append("final_urls must contain at least one non-empty URL")

    if not isinstance(headlines, list):
        errors.append("headlines must be a list")
        return errors
    if len(headlines) < _HEADLINE_MIN or len(headlines) > _HEADLINE_MAX:
        errors.append(
            f"headlines: expected {_HEADLINE_MIN}..{_HEADLINE_MAX} items, got {len(headlines)}"
        )
    for i, h in enumerate(headlines):
        if isinstance(h, str):
            text = h
            pinned = None
        elif isinstance(h, dict):
            text = h.get("text", "")
            pinned = (h.get("pinned_field") or h.get("pinnedField") or "") or None
            if pinned is not None and pinned.upper() not in _VALID_PINNED_FIELDS:
                errors.append(
                    f"headlines[{i}]: pinned_field {pinned!r} not in {sorted(_VALID_PINNED_FIELDS)}"
                )
        else:
            errors.append(f"headlines[{i}]: must be str or dict, got {type(h).__name__}")
            continue
        if not text or not text.strip():
            errors.append(f"headlines[{i}]: text is required")
        elif len(text) > _HEADLINE_CHAR_LIMIT:
            errors.append(
                f"headlines[{i}]: text is {len(text)} chars, limit is {_HEADLINE_CHAR_LIMIT}"
            )

    if not isinstance(descriptions, list):
        errors.append("descriptions must be a list")
        return errors
    if len(descriptions) < _DESCRIPTION_MIN or len(descriptions) > _DESCRIPTION_MAX:
        errors.append(
            f"descriptions: expected {_DESCRIPTION_MIN}..{_DESCRIPTION_MAX} items, got {len(descriptions)}"
        )
    for i, d in enumerate(descriptions):
        if not isinstance(d, str) or not d.strip():
            errors.append(f"descriptions[{i}]: text is required (string)")
        elif len(d) > _DESCRIPTION_CHAR_LIMIT:
            errors.append(
                f"descriptions[{i}]: text is {len(d)} chars, limit is {_DESCRIPTION_CHAR_LIMIT}"
            )

    for label, value in (("path1", path1), ("path2", path2)):
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            errors.append(f"{label}: must be string or None")
            continue
        if len(value) > _PATH_CHAR_LIMIT:
            errors.append(
                f"{label}: {len(value)} chars, limit is {_PATH_CHAR_LIMIT}"
            )
        if not _is_latin_path(value):
            errors.append(f"{label}: must contain only ASCII letters/digits/hyphen/underscore")

    return errors


@mcp.tool()
async def create_responsive_search_ad(
    customer_id: Annotated[
        str,
        Field(description="Google Ads customer ID (10 digits, no dashes)."),
    ],
    ad_group_id: Annotated[
        str,
        Field(description="Parent ad group ID."),
    ],
    headlines: Annotated[
        list,
        Field(description="3-15 items. Each item is either a string (unpinned) or a dict {text, pinned_field}. Each text <=30 chars."),
    ],
    descriptions: Annotated[
        list,
        Field(description="2-4 items. Each is a string <=90 chars."),
    ],
    final_urls: Annotated[
        list,
        Field(description="One or more final URLs (strings)."),
    ],
    path1: Annotated[
        str,
        Field(description="Optional display URL path1 — ASCII only, <=15 chars."),
    ] = "",
    path2: Annotated[
        str,
        Field(description="Optional display URL path2 — ASCII only, <=15 chars."),
    ] = "",
    validate_only: Annotated[
        bool,
        Field(description="If True (default), Google validates but does not apply."),
    ] = True,
) -> dict:
    """Create a Responsive Search Ad inside an ad group (adGroupAds:mutate).

    Client-side validation runs before any API call so silly mistakes
    (too few headlines, text over Google''s character limits, Cyrillic
    characters in path slugs) fail fast with a readable error list
    instead of a generic Google INVALID_ARGUMENT error.

    Limits enforced:
    - Headlines: 3-15 items, each <=30 chars.
    - Descriptions: 2-4 items, each <=90 chars.
    - Path1/path2: <=15 chars, ASCII letters/digits/hyphen/underscore.
    - Pinned headline positions limited to HEADLINE_{1,2,3} and
      DESCRIPTION_{1,2} (Google schema).

    The created ad is always PAUSED so operators can preview before
    enabling. Status flip is a separate workflow (UI or future
    ``update_ad_group_ad`` tool).
    """
    errors = _validate_rsa_inputs(headlines, descriptions, final_urls, path1 or None, path2 or None)
    if errors:
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": errors,
            "raw": {},
        }

    formatted_customer_id = format_customer_id(customer_id)
    ad_group_resource = (
        f"customers/{formatted_customer_id}/adGroups/{ad_group_id}"
    )

    def _headline_payload(h):
        if isinstance(h, str):
            return {"text": h}
        out = {"text": h.get("text", "")}
        pinned = (h.get("pinned_field") or h.get("pinnedField") or "")
        if pinned:
            out["pinnedField"] = pinned.upper()
        return out

    ad_payload: dict[str, Any] = {
        "finalUrls": [u for u in final_urls if (u or "").strip()],
        "responsiveSearchAd": {
            "headlines": [_headline_payload(h) for h in headlines],
            "descriptions": [{"text": d} for d in descriptions],
        },
    }
    if path1:
        ad_payload["responsiveSearchAd"]["path1"] = path1
    if path2:
        ad_payload["responsiveSearchAd"]["path2"] = path2

    operation = {
        "create": {
            "adGroup": ad_group_resource,
            "status": "PAUSED",
            "ad": ad_payload,
        }
    }

    client = GoogleAdsClient()
    raw = client.mutate(
        customer_id=formatted_customer_id,
        resource="adGroupAds",
        operations=[operation],
        validate_only=validate_only,
    )
    return _normalize_response(raw, validate_only)


# ----------------------------------------------------------------------------
# create_campaign (3 sequential REST calls, budget -> campaign -> criteria)
# ----------------------------------------------------------------------------

_KYIV_GEO_CONSTANT = "1012959"   # Kyiv
_LANG_UK = "1002"                # Ukrainian
_LANG_EN = "1000"                # English

_ALLOWED_CHANNEL_TYPES = {"SEARCH", "DISPLAY", "PERFORMANCE_MAX"}
_ALLOWED_CREATE_BID_STRATEGIES = {"MANUAL_CPC", "MAXIMIZE_CONVERSIONS"}


def _default_config() -> dict:
    return {
        "channel_type": "SEARCH",
        "bid_strategy": "MANUAL_CPC",
        "cpc_bid_ceiling_micros": None,
        "geo_target_constants": [_KYIV_GEO_CONSTANT],
        "language_constants": [_LANG_UK, _LANG_EN],
        "network_settings": {
            "targetGoogleSearch": True,
            "targetSearchNetwork": False,
            "targetContentNetwork": False,
            "targetPartnerSearchNetwork": False,
        },
    }


def _validate_create_campaign_config(name: str, daily_budget_micros: int, config: dict) -> list[str]:
    errors: list[str] = []
    if not (name or "").strip():
        errors.append("name is required")
    if daily_budget_micros is None or int(daily_budget_micros) <= 0:
        errors.append("daily_budget_micros must be > 0")

    channel = (config.get("channel_type") or "SEARCH").upper()
    if channel not in _ALLOWED_CHANNEL_TYPES:
        errors.append(f"channel_type must be one of {sorted(_ALLOWED_CHANNEL_TYPES)}")
    strategy = (config.get("bid_strategy") or "MANUAL_CPC").upper()
    if strategy not in _ALLOWED_CREATE_BID_STRATEGIES:
        errors.append(
            f"bid_strategy must be one of {sorted(_ALLOWED_CREATE_BID_STRATEGIES)} on creation"
        )
    if strategy == "MANUAL_CPC":
        ceiling = config.get("cpc_bid_ceiling_micros")
        if ceiling is None or int(ceiling) <= 0:
            errors.append("MANUAL_CPC requires cpc_bid_ceiling_micros > 0 in config")
    if not config.get("geo_target_constants"):
        errors.append("geo_target_constants must include at least one geo_target_constant ID")
    if not config.get("language_constants"):
        errors.append("language_constants must include at least one language_constant ID")
    return errors


@mcp.tool()
async def create_campaign(
    customer_id: Annotated[
        str,
        Field(description="Google Ads customer ID (10 digits, no dashes)."),
    ],
    name: Annotated[
        str,
        Field(description="Campaign name. Example: 'CREAVY — UA Search v1'."),
    ],
    daily_budget_micros: Annotated[
        int,
        Field(description="Daily budget in micros. 50_000_000 = 50 UAH/day."),
    ],
    config: Annotated[
        dict,
        Field(description="Optional overrides for channel_type, bid_strategy, cpc_bid_ceiling_micros, geo_target_constants, language_constants, network_settings. Defaults to Kyiv geo + UA/EN langs + SEARCH + MANUAL_CPC."),
    ] = {},
    validate_only: Annotated[
        bool,
        Field(description="If True (default), Google validates each step but does not apply."),
    ] = True,
) -> dict:
    """Create a full SEARCH campaign: budget -> campaign -> geo/lang criteria.

    Highest-risk tool in Phase 1: three sequential REST calls. The
    new campaign is ALWAYS created with ``status=PAUSED`` so nothing
    spends until the operator reviews and explicitly enables.

    Sequence:

    1. ``campaignBudgets:mutate`` (create) — returns the budget resource
       name. If this fails, we return immediately — nothing else to
       roll back.
    2. ``campaigns:mutate`` (create) — references the new budget and
       carries the bidding strategy. If this fails but step 1 succeeded,
       the envelope flags the orphan budget under
       ``warnings`` so the caller can decide whether to clean up.
    3. (Optional) ``campaignCriteria:mutate`` — one create op per geo
       target and one per language. Skipped only if both lists are
       empty.

    Args:
        customer_id: Google Ads customer ID.
        name: Campaign name.
        daily_budget_micros: New budget in micros.
        config: See module-level defaults. Safe to pass ``{}``.
        validate_only: Default True.

    Returns:
        CREAVY mutate envelope. ``resource_names`` lists [budget,
        campaign, *criteria]. ``raw`` contains the three raw API
        responses under keys ``budget``, ``campaign``, ``criteria``.
    """
    merged = _default_config()
    merged.update(config or {})

    errors = _validate_create_campaign_config(name, daily_budget_micros, merged)
    if errors:
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": errors,
            "raw": {},
        }

    formatted_customer_id = format_customer_id(customer_id)
    client = GoogleAdsClient()
    warnings: list[str] = []
    resource_names: list[str] = []
    raw_steps: dict[str, Any] = {}

    # Step 1: budget.
    budget_name = f"{name.strip()} — budget"
    budget_op = {
        "create": {
            "name": budget_name,
            "amountMicros": str(int(daily_budget_micros)),
            "deliveryMethod": "STANDARD",
            "explicitlyShared": False,
        }
    }
    budget_resp = client.mutate(
        customer_id=formatted_customer_id,
        resource="campaignBudgets",
        operations=[budget_op],
        validate_only=validate_only,
    )
    raw_steps["budget"] = budget_resp
    if "error" in budget_resp and "status_code" in budget_resp:
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": [f"step 1 (budget) HTTP {budget_resp.get('status_code')}: {budget_resp.get('error')}"],
            "raw": raw_steps,
        }
    budget_rn = None
    for r in (budget_resp.get("results") or []):
        if r.get("resourceName"):
            budget_rn = r["resourceName"]
            break
    if not budget_rn and not validate_only:
        warnings.append("step 1 (budget): no resourceName returned; cannot continue")
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": [],
            "partial_failures": [],
            "warnings": warnings,
            "raw": raw_steps,
        }
    # In validate_only mode Google does not return a real resourceName,
    # so we synthesise a placeholder that lets step 2 format a valid request.
    budget_ref_for_step2 = budget_rn or f"customers/{formatted_customer_id}/campaignBudgets/VALIDATE_ONLY"
    if budget_rn:
        resource_names.append(budget_rn)

    # Step 2: campaign.
    campaign_body: dict[str, Any] = {
        "name": name.strip(),
        "status": "PAUSED",
        "advertisingChannelType": merged["channel_type"].upper(),
        "campaignBudget": budget_ref_for_step2,
        "networkSettings": merged["network_settings"],
    }
    strategy = merged["bid_strategy"].upper()
    if strategy == "MANUAL_CPC":
        campaign_body["manualCpc"] = {"enhancedCpcEnabled": False}
        campaign_body["bidCeilingMicros"] = str(int(merged["cpc_bid_ceiling_micros"]))
    elif strategy == "MAXIMIZE_CONVERSIONS":
        campaign_body["maximizeConversions"] = {}

    campaign_op = {"create": campaign_body}
    campaign_resp = client.mutate(
        customer_id=formatted_customer_id,
        resource="campaigns",
        operations=[campaign_op],
        validate_only=validate_only,
    )
    raw_steps["campaign"] = campaign_resp
    if "error" in campaign_resp and "status_code" in campaign_resp:
        msg = f"step 2 (campaign) HTTP {campaign_resp.get('status_code')}: {campaign_resp.get('error')}"
        if budget_rn:
            msg += f" — budget {budget_rn} was created and may need cleanup"
        return {
            "success": False,
            "validate_only": validate_only,
            "resource_names": resource_names,
            "partial_failures": [],
            "warnings": [msg],
            "raw": raw_steps,
        }
    campaign_rn = None
    for r in (campaign_resp.get("results") or []):
        if r.get("resourceName"):
            campaign_rn = r["resourceName"]
            break
    if campaign_rn:
        resource_names.append(campaign_rn)
    # In validate_only mode synthesise a placeholder for step 3.
    campaign_ref_for_step3 = (
        campaign_rn or f"customers/{formatted_customer_id}/campaigns/VALIDATE_ONLY"
    )

    # Step 3: geo + language criteria. Skipped only if both are empty.
    criteria_ops: list[dict] = []
    for geo in merged.get("geo_target_constants") or []:
        criteria_ops.append({
            "create": {
                "campaign": campaign_ref_for_step3,
                "location": {
                    "geoTargetConstant": f"geoTargetConstants/{geo}",
                },
            }
        })
    for lang in merged.get("language_constants") or []:
        criteria_ops.append({
            "create": {
                "campaign": campaign_ref_for_step3,
                "language": {
                    "languageConstant": f"languageConstants/{lang}",
                },
            }
        })

    if criteria_ops:
        crit_resp = client.mutate(
            customer_id=formatted_customer_id,
            resource="campaignCriteria",
            operations=criteria_ops,
            validate_only=validate_only,
        )
        raw_steps["criteria"] = crit_resp
        if "error" in crit_resp and "status_code" in crit_resp:
            warnings.append(
                f"step 3 (criteria) HTTP {crit_resp.get('status_code')}: {crit_resp.get('error')} "
                f"— campaign {campaign_rn} exists but has no geo/lang targeting"
            )
        else:
            for r in (crit_resp.get("results") or []):
                if r.get("resourceName"):
                    resource_names.append(r["resourceName"])

    envelope = {
        "success": True,
        "validate_only": validate_only,
        "resource_names": resource_names,
        "partial_failures": [],
        "warnings": warnings,
        "raw": raw_steps,
    }
    return envelope
