"""Authentication helpers for the Google Ads REST API.

Extracted verbatim from the upstream `google_ads_server.py` monolith.
No functional changes — env vars are now read from `creavy_ads.config`.
"""

import json
import logging
import os

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from creavy_ads import config
from creavy_ads.config import (
    GOOGLE_ADS_AUTH_TYPE,
    GOOGLE_ADS_CREDENTIALS_PATH,
    GOOGLE_ADS_DEVELOPER_TOKEN,
    GOOGLE_ADS_LOGIN_CUSTOMER_ID,
    SCOPES,
)

logger = logging.getLogger('google_ads_server')


def format_customer_id(customer_id: str) -> str:
    """Format customer ID to ensure it's 10 digits without dashes."""
    # Convert to string if passed as integer or another type
    customer_id = str(customer_id)

    # Remove any quotes surrounding the customer_id (both escaped and unescaped)
    customer_id = customer_id.replace('\"', '').replace('"', '')

    # Remove any non-digit characters (including dashes, braces, etc.)
    customer_id = ''.join(char for char in customer_id if char.isdigit())

    # Ensure it's 10 digits with leading zeros if needed
    return customer_id.zfill(10)


def get_credentials():
    """
    Get and refresh OAuth credentials or service account credentials based on the auth type.

    This function supports two authentication methods:
    1. OAuth 2.0 (User Authentication) - For individual users or desktop applications
    2. Service Account (Server-to-Server Authentication) - For automated systems

    Returns:
        Valid credentials object to use with Google Ads API
    """
    if not GOOGLE_ADS_CREDENTIALS_PATH:
        raise ValueError("GOOGLE_ADS_CREDENTIALS_PATH environment variable not set")

    auth_type = GOOGLE_ADS_AUTH_TYPE.lower()
    logger.info(f"Using authentication type: {auth_type}")

    # Service Account authentication
    if auth_type == "service_account":
        try:
            return get_service_account_credentials()
        except Exception as e:
            logger.error(f"Error with service account authentication: {str(e)}")
            raise

    # OAuth 2.0 authentication (default)
    return get_oauth_credentials()


def get_service_account_credentials():
    """Get credentials using a service account key file."""
    logger.info(f"Loading service account credentials from {GOOGLE_ADS_CREDENTIALS_PATH}")

    if not os.path.exists(GOOGLE_ADS_CREDENTIALS_PATH):
        raise FileNotFoundError(f"Service account key file not found at {GOOGLE_ADS_CREDENTIALS_PATH}")

    try:
        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_ADS_CREDENTIALS_PATH,
            scopes=SCOPES
        )

        # Check if impersonation is required
        impersonation_email = os.environ.get("GOOGLE_ADS_IMPERSONATION_EMAIL")
        if impersonation_email:
            logger.info(f"Impersonating user: {impersonation_email}")
            credentials = credentials.with_subject(impersonation_email)

        return credentials

    except Exception as e:
        logger.error(f"Error loading service account credentials: {str(e)}")
        raise


def get_oauth_credentials():
    """Get and refresh OAuth user credentials."""
    creds = None
    client_config = None

    # Path to store the refreshed token
    token_path = GOOGLE_ADS_CREDENTIALS_PATH
    if os.path.exists(token_path) and not os.path.basename(token_path).endswith('.json'):
        # If it's not explicitly a .json file, append a default name
        token_dir = os.path.dirname(token_path)
        token_path = os.path.join(token_dir, 'google_ads_token.json')

    # Check if token file exists and load credentials
    if os.path.exists(token_path):
        try:
            logger.info(f"Loading OAuth credentials from {token_path}")
            with open(token_path, 'r') as f:
                creds_data = json.load(f)
                # Check if this is a client config or saved credentials
                if "installed" in creds_data or "web" in creds_data:
                    client_config = creds_data
                    logger.info("Found OAuth client configuration")
                else:
                    logger.info("Found existing OAuth token")
                    creds = Credentials.from_authorized_user_info(creds_data, SCOPES)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in token file: {token_path}")
            creds = None
        except Exception as e:
            logger.warning(f"Error loading credentials: {str(e)}")
            creds = None

    # If credentials don't exist or are invalid, get new ones
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                logger.info("Refreshing expired token")
                creds.refresh(Request())
                logger.info("Token successfully refreshed")
            except RefreshError as e:
                logger.warning(f"Error refreshing token: {str(e)}, will try to get new token")
                creds = None
            except Exception as e:
                logger.error(f"Unexpected error refreshing token: {str(e)}")
                raise

        # If we need new credentials
        if not creds:
            # If no client_config is defined yet, create one from environment variables
            if not client_config:
                logger.info("Creating OAuth client config from environment variables")
                client_id = os.environ.get("GOOGLE_ADS_CLIENT_ID")
                client_secret = os.environ.get("GOOGLE_ADS_CLIENT_SECRET")

                if not client_id or not client_secret:
                    raise ValueError("GOOGLE_ADS_CLIENT_ID and GOOGLE_ADS_CLIENT_SECRET must be set if no client config file exists")

                client_config = {
                    "installed": {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"]
                    }
                }

            # Run the OAuth flow
            logger.info("Starting OAuth authentication flow")
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=0)
            logger.info("OAuth flow completed successfully")

        # Save the refreshed/new credentials
        try:
            logger.info(f"Saving credentials to {token_path}")
            # Ensure directory exists
            os.makedirs(os.path.dirname(token_path), exist_ok=True)
            with open(token_path, 'w') as f:
                f.write(creds.to_json())
        except Exception as e:
            logger.warning(f"Could not save credentials: {str(e)}")

    return creds


def get_headers(creds):
    """Get headers for Google Ads API requests."""
    if not GOOGLE_ADS_DEVELOPER_TOKEN:
        raise ValueError("GOOGLE_ADS_DEVELOPER_TOKEN environment variable not set")

    # Handle different credential types
    if isinstance(creds, service_account.Credentials):
        # For service account, we need to get a new bearer token
        auth_req = Request()
        creds.refresh(auth_req)
        token = creds.token
    else:
        # For OAuth credentials, check if token needs refresh
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                try:
                    logger.info("Refreshing expired OAuth token in get_headers")
                    creds.refresh(Request())
                    logger.info("Token successfully refreshed in get_headers")
                except RefreshError as e:
                    logger.error(f"Error refreshing token in get_headers: {str(e)}")
                    raise ValueError(f"Failed to refresh OAuth token: {str(e)}")
                except Exception as e:
                    logger.error(f"Unexpected error refreshing token in get_headers: {str(e)}")
                    raise
            else:
                raise ValueError("OAuth credentials are invalid and cannot be refreshed")

        token = creds.token

    headers = {
        'Authorization': f'Bearer {token}',
        'developer-token': GOOGLE_ADS_DEVELOPER_TOKEN,
        'content-type': 'application/json'
    }

    if GOOGLE_ADS_LOGIN_CUSTOMER_ID:
        headers['login-customer-id'] = format_customer_id(GOOGLE_ADS_LOGIN_CUSTOMER_ID)

    return headers
