"""Environment-driven configuration for the CREAVY Ads MCP server.

Centralises the env-var reads that used to live at the top of
`google_ads_server.py`. Side-effect free except for the optional
`.env` load, which mirrors upstream behaviour.
"""

import logging
import os

logger = logging.getLogger('google_ads_server')

# Constants and configuration
SCOPES = ['https://www.googleapis.com/auth/adwords']
API_VERSION = "v23"  # Google Ads API version

# Load environment variables
try:
    from dotenv import load_dotenv
    # Load from .env file if it exists
    load_dotenv()
    logger.info("Environment variables loaded from .env file")
except ImportError:
    logger.warning("python-dotenv not installed, skipping .env file loading")

# Get credentials from environment variables
GOOGLE_ADS_CREDENTIALS_PATH = os.environ.get("GOOGLE_ADS_CREDENTIALS_PATH")
GOOGLE_ADS_DEVELOPER_TOKEN = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN")
GOOGLE_ADS_LOGIN_CUSTOMER_ID = os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "")
GOOGLE_ADS_AUTH_TYPE = os.environ.get("GOOGLE_ADS_AUTH_TYPE", "oauth")  # oauth or service_account
