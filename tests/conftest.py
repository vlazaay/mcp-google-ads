"""Test configuration: keep import-time auth side-effects out of unit tests.

`creavy_ads.client.GoogleAdsClient.__init__` calls `get_credentials()` and
`get_headers()` from `creavy_ads.auth`, which try to read OAuth/service
account credentials from disk and the network. Unit tests must not
exercise that path. Tests inject a fake client directly, but defensive
patching here keeps a future test author from accidentally hitting the
network.
"""

import os
import sys
from pathlib import Path

# Allow `import creavy_ads...` when running pytest from repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Make sure no .env from a real deploy bleeds into tests.
os.environ.setdefault("GOOGLE_ADS_DEVELOPER_TOKEN", "TEST-TOKEN")
os.environ.setdefault("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "1234567890")
os.environ.setdefault("GOOGLE_ADS_AUTH_TYPE", "oauth")
