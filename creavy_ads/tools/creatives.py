"""Ad-creative inspection tools: get_ad_creatives, get_account_currency.

Bodies copied verbatim from the upstream monolith.
"""

import logging

import requests
from google.auth.transport.requests import Request
from pydantic import Field

from creavy_ads.auth import format_customer_id, get_credentials, get_headers
from creavy_ads.config import API_VERSION
from creavy_ads.server import mcp

logger = logging.getLogger('google_ads_server')


@mcp.tool()
async def get_ad_creatives(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'")
) -> str:
    """
    Get ad creative details including headlines, descriptions, and URLs.
    
    This tool retrieves the actual ad content (headlines, descriptions) 
    for review and analysis. Great for creative audits.
    
    RECOMMENDED WORKFLOW:
    1. First run list_accounts() to get available account IDs
    2. Then run this command with the desired account ID
    
    Args:
        customer_id: The Google Ads customer ID as a string (10 digits, no dashes)
        
    Returns:
        Formatted list of ad creative details
        
    Example:
        customer_id: "1234567890"
    """
    query = """
        SELECT
            ad_group_ad.ad.id,
            ad_group_ad.ad.name,
            ad_group_ad.ad.type,
            ad_group_ad.ad.final_urls,
            ad_group_ad.status,
            ad_group_ad.ad.responsive_search_ad.headlines,
            ad_group_ad.ad.responsive_search_ad.descriptions,
            ad_group.name,
            campaign.name
        FROM ad_group_ad
        WHERE ad_group_ad.status != 'REMOVED'
        ORDER BY campaign.name, ad_group.name
        LIMIT 50
    """
    
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error retrieving ad creatives: {response.text}"
        
        results = response.json()
        if not results.get('results'):
            return "No ad creatives found for this customer ID."
        
        # Format the results in a readable way
        output_lines = [f"Ad Creatives for Customer ID {formatted_customer_id}:"]
        output_lines.append("=" * 80)
        
        for i, result in enumerate(results['results'], 1):
            ad = result.get('adGroupAd', {}).get('ad', {})
            ad_group = result.get('adGroup', {})
            campaign = result.get('campaign', {})
            
            output_lines.append(f"\n{i}. Campaign: {campaign.get('name', 'N/A')}")
            output_lines.append(f"   Ad Group: {ad_group.get('name', 'N/A')}")
            output_lines.append(f"   Ad ID: {ad.get('id', 'N/A')}")
            output_lines.append(f"   Ad Name: {ad.get('name', 'N/A')}")
            output_lines.append(f"   Status: {result.get('adGroupAd', {}).get('status', 'N/A')}")
            output_lines.append(f"   Type: {ad.get('type', 'N/A')}")
            
            # Handle Responsive Search Ads
            rsa = ad.get('responsiveSearchAd', {})
            if rsa:
                if 'headlines' in rsa:
                    output_lines.append("   Headlines:")
                    for headline in rsa['headlines']:
                        output_lines.append(f"     - {headline.get('text', 'N/A')}")
                
                if 'descriptions' in rsa:
                    output_lines.append("   Descriptions:")
                    for desc in rsa['descriptions']:
                        output_lines.append(f"     - {desc.get('text', 'N/A')}")
            
            # Handle Final URLs
            final_urls = ad.get('finalUrls', [])
            if final_urls:
                output_lines.append(f"   Final URLs: {', '.join(final_urls)}")
            
            output_lines.append("-" * 80)
        
        return "\n".join(output_lines)
    
    except Exception as e:
        return f"Error retrieving ad creatives: {str(e)}"

@mcp.tool()
async def get_account_currency(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'")
) -> str:
    """
    Retrieve the default currency code used by the Google Ads account.
    
    IMPORTANT: Run this first before analyzing cost data to understand which currency
    the account uses. Cost values are always displayed in the account's currency.
    
    Args:
        customer_id: The Google Ads customer ID as a string (10 digits, no dashes)
    
    Returns:
        The account's default currency code (e.g., 'USD', 'EUR', 'GBP')
        
    Example:
        customer_id: "1234567890"
    """
    query = """
        SELECT
            customer.id,
            customer.currency_code
        FROM customer
        LIMIT 1
    """
    
    try:
        creds = get_credentials()
        
        # Force refresh if needed
        if not creds.valid:
            logger.info("Credentials not valid, attempting refresh...")
            if hasattr(creds, 'refresh_token') and creds.refresh_token:
                creds.refresh(Request())
                logger.info("Credentials refreshed successfully")
            else:
                raise ValueError("Invalid credentials and no refresh token available")
        
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error retrieving account currency: {response.text}"
        
        results = response.json()
        if not results.get('results'):
            return "No account information found for this customer ID."
        
        # Extract the currency code from the results
        customer = results['results'][0].get('customer', {})
        currency_code = customer.get('currencyCode', 'Not specified')
        
        return f"Account {formatted_customer_id} uses currency: {currency_code}"
    
    except Exception as e:
        logger.error(f"Error retrieving account currency: {str(e)}")
        return f"Error retrieving account currency: {str(e)}"
