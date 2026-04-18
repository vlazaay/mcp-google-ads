"""Image/asset tooling: get_image_assets, download_image_asset,
get_asset_usage, analyze_image_assets.

Bodies copied verbatim from the upstream monolith.
"""

import logging
from pathlib import Path

import requests
from pydantic import Field

from creavy_ads.auth import format_customer_id, get_credentials, get_headers
from creavy_ads.config import API_VERSION
from creavy_ads.server import mcp

logger = logging.getLogger('google_ads_server')


@mcp.tool()
async def get_image_assets(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'"),
    limit: int = Field(default=50, description="Maximum number of image assets to return")
) -> str:
    """
    Retrieve all image assets in the account including their full-size URLs.
    
    This tool allows you to get details about image assets used in your Google Ads account,
    including the URLs to download the full-size images for further processing or analysis.
    
    RECOMMENDED WORKFLOW:
    1. First run list_accounts() to get available account IDs
    2. Then run this command with the desired account ID
    
    Args:
        customer_id: The Google Ads customer ID as a string (10 digits, no dashes)
        limit: Maximum number of image assets to return (default: 50)
        
    Returns:
        Formatted list of image assets with their download URLs
        
    Example:
        customer_id: "1234567890"
        limit: 100
    """
    query = f"""
        SELECT
            asset.id,
            asset.name,
            asset.type,
            asset.image_asset.full_size.url,
            asset.image_asset.full_size.height_pixels,
            asset.image_asset.full_size.width_pixels,
            asset.image_asset.file_size
        FROM
            asset
        WHERE
            asset.type = 'IMAGE'
        LIMIT {limit}
    """
    
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error retrieving image assets: {response.text}"
        
        results = response.json()
        if not results.get('results'):
            return "No image assets found for this customer ID."
        
        # Format the results in a readable way
        output_lines = [f"Image Assets for Customer ID {formatted_customer_id}:"]
        output_lines.append("=" * 80)
        
        for i, result in enumerate(results['results'], 1):
            asset = result.get('asset', {})
            image_asset = asset.get('imageAsset', {})
            full_size = image_asset.get('fullSize', {})
            
            output_lines.append(f"\n{i}. Asset ID: {asset.get('id', 'N/A')}")
            output_lines.append(f"   Name: {asset.get('name', 'N/A')}")
            
            if full_size:
                output_lines.append(f"   Image URL: {full_size.get('url', 'N/A')}")
                output_lines.append(f"   Dimensions: {full_size.get('widthPixels', 'N/A')} x {full_size.get('heightPixels', 'N/A')} px")
            
            file_size = image_asset.get('fileSize', 'N/A')
            if file_size != 'N/A':
                # Convert to KB for readability
                file_size_kb = int(file_size) / 1024
                output_lines.append(f"   File Size: {file_size_kb:.2f} KB")
            
            output_lines.append("-" * 80)
        
        return "\n".join(output_lines)
    
    except Exception as e:
        return f"Error retrieving image assets: {str(e)}"

@mcp.tool()
async def download_image_asset(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'"),
    asset_id: str = Field(description="The ID of the image asset to download"),
    output_dir: str = Field(default="./ad_images", description="Directory to save the downloaded image")
) -> str:
    """
    Download a specific image asset from a Google Ads account.
    
    This tool allows you to download the full-size version of an image asset
    for further processing, analysis, or backup.
    
    RECOMMENDED WORKFLOW:
    1. First run list_accounts() to get available account IDs
    2. Then run get_image_assets() to get available image asset IDs
    3. Finally use this command to download specific images
    
    Args:
        customer_id: The Google Ads customer ID as a string (10 digits, no dashes)
        asset_id: The ID of the image asset to download
        output_dir: Directory where the image should be saved (default: ./ad_images)
        
    Returns:
        Status message indicating success or failure of the download
        
    Example:
        customer_id: "1234567890"
        asset_id: "12345"
        output_dir: "./my_ad_images"
    """
    query = f"""
        SELECT
            asset.id,
            asset.name,
            asset.image_asset.full_size.url
        FROM
            asset
        WHERE
            asset.type = 'IMAGE'
            AND asset.id = {asset_id}
        LIMIT 1
    """
    
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error retrieving image asset: {response.text}"
        
        results = response.json()
        if not results.get('results'):
            return f"No image asset found with ID {asset_id}"
        
        # Extract the image URL
        asset = results['results'][0].get('asset', {})
        image_url = asset.get('imageAsset', {}).get('fullSize', {}).get('url')
        asset_name = asset.get('name', f"image_{asset_id}")
        
        if not image_url:
            return f"No download URL found for image asset ID {asset_id}"
        
        # Validate and sanitize the output directory to prevent path traversal
        try:
            # Get the base directory (current working directory)
            base_dir = Path.cwd()
            # Resolve the output directory to an absolute path
            resolved_output_dir = Path(output_dir).resolve()
            
            # Ensure the resolved path is within or under the current working directory
            # This prevents path traversal attacks like "../../../etc"
            try:
                resolved_output_dir.relative_to(base_dir)
            except ValueError:
                # If the path is not relative to base_dir, use the default safe directory
                resolved_output_dir = base_dir / "ad_images"
                logger.warning(f"Invalid output directory '{output_dir}' - using default './ad_images'")
            
            # Create output directory if it doesn't exist
            resolved_output_dir.mkdir(parents=True, exist_ok=True)
            
        except Exception as e:
            return f"Error creating output directory: {str(e)}"
        
        # Download the image
        image_response = requests.get(image_url)
        if image_response.status_code != 200:
            return f"Failed to download image: HTTP {image_response.status_code}"
        
        # Clean the filename to be safe for filesystem
        safe_name = ''.join(c for c in asset_name if c.isalnum() or c in ' ._-')
        filename = f"{asset_id}_{safe_name}.jpg"
        file_path = resolved_output_dir / filename
        
        # Save the image
        with open(file_path, 'wb') as f:
            f.write(image_response.content)
        
        return f"Successfully downloaded image asset {asset_id} to {file_path}"
    
    except Exception as e:
        return f"Error downloading image asset: {str(e)}"

@mcp.tool()
async def get_asset_usage(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'"),
    asset_id: str = Field(default=None, description="Optional: specific asset ID to look up (leave empty to get all image assets)"),
    asset_type: str = Field(default="IMAGE", description="Asset type to search for ('IMAGE', 'TEXT', 'VIDEO', etc.)")
) -> str:
    """
    Find where specific assets are being used in campaigns, ad groups, and ads.
    
    This tool helps you analyze how assets are linked to campaigns and ads across your account,
    which is useful for creative analysis and optimization.
    
    RECOMMENDED WORKFLOW:
    1. First run list_accounts() to get available account IDs
    2. Run get_image_assets() to see available assets
    3. Use this command to see where specific assets are used
    
    Args:
        customer_id: The Google Ads customer ID as a string (10 digits, no dashes)
        asset_id: Optional specific asset ID to look up (leave empty to get all assets of the specified type)
        asset_type: Type of asset to search for (default: 'IMAGE')
        
    Returns:
        Formatted report showing where assets are used in the account
        
    Example:
        customer_id: "1234567890"
        asset_id: "12345"
        asset_type: "IMAGE"
    """
    # Build the query based on whether a specific asset ID was provided
    where_clause = f"asset.type = '{asset_type}'"
    if asset_id:
        where_clause += f" AND asset.id = {asset_id}"
    
    # First get the assets themselves
    assets_query = f"""
        SELECT
            asset.id,
            asset.name,
            asset.type
        FROM
            asset
        WHERE
            {where_clause}
        LIMIT 100
    """
    
    # Then get the associations between assets and campaigns/ad groups
    # Try using campaign_asset instead of asset_link
    associations_query = f"""
        SELECT
            campaign.id,
            campaign.name,
            asset.id,
            asset.name,
            asset.type
        FROM
            campaign_asset
        WHERE
            {where_clause}
        LIMIT 500
    """

    # Also try ad_group_asset for ad group level information
    ad_group_query = f"""
        SELECT
            ad_group.id,
            ad_group.name,
            asset.id,
            asset.name,
            asset.type
        FROM
            ad_group_asset
        WHERE
            {where_clause}
        LIMIT 500
    """
    
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        
        # First get the assets
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        payload = {"query": assets_query}
        assets_response = requests.post(url, headers=headers, json=payload)
        
        if assets_response.status_code != 200:
            return f"Error retrieving assets: {assets_response.text}"
        
        assets_results = assets_response.json()
        if not assets_results.get('results'):
            return f"No {asset_type} assets found for this customer ID."
        
        # Now get the associations
        payload = {"query": associations_query}
        assoc_response = requests.post(url, headers=headers, json=payload)
        
        if assoc_response.status_code != 200:
            return f"Error retrieving asset associations: {assoc_response.text}"
        
        assoc_results = assoc_response.json()
        
        # Format the results in a readable way
        output_lines = [f"Asset Usage for Customer ID {formatted_customer_id}:"]
        output_lines.append("=" * 80)
        
        # Create a dictionary to organize asset usage by asset ID
        asset_usage = {}
        
        # Initialize the asset usage dictionary with basic asset info
        for result in assets_results.get('results', []):
            asset = result.get('asset', {})
            asset_id = asset.get('id')
            if asset_id:
                asset_usage[asset_id] = {
                    'name': asset.get('name', 'Unnamed asset'),
                    'type': asset.get('type', 'Unknown'),
                    'usage': []
                }
        
        # Add usage information from the associations
        for result in assoc_results.get('results', []):
            asset = result.get('asset', {})
            asset_id = asset.get('id')
            
            if asset_id and asset_id in asset_usage:
                campaign = result.get('campaign', {})
                ad_group = result.get('adGroup', {})
                ad = result.get('adGroupAd', {}).get('ad', {}) if 'adGroupAd' in result else {}
                asset_link = result.get('assetLink', {})
                
                usage_info = {
                    'campaign_id': campaign.get('id', 'N/A'),
                    'campaign_name': campaign.get('name', 'N/A'),
                    'ad_group_id': ad_group.get('id', 'N/A'),
                    'ad_group_name': ad_group.get('name', 'N/A'),
                    'ad_id': ad.get('id', 'N/A') if ad else 'N/A',
                    'ad_name': ad.get('name', 'N/A') if ad else 'N/A'
                }
                
                asset_usage[asset_id]['usage'].append(usage_info)
        
        # Format the output
        for asset_id, info in asset_usage.items():
            output_lines.append(f"\nAsset ID: {asset_id}")
            output_lines.append(f"Name: {info['name']}")
            output_lines.append(f"Type: {info['type']}")
            
            if info['usage']:
                output_lines.append("\nUsed in:")
                output_lines.append("-" * 60)
                output_lines.append(f"{'Campaign':<30} | {'Ad Group':<30}")
                output_lines.append("-" * 60)
                
                for usage in info['usage']:
                    campaign_str = f"{usage['campaign_name']} ({usage['campaign_id']})"
                    ad_group_str = f"{usage['ad_group_name']} ({usage['ad_group_id']})"
                    
                    output_lines.append(f"{campaign_str[:30]:<30} | {ad_group_str[:30]:<30}")
            
            output_lines.append("=" * 80)
        
        return "\n".join(output_lines)
    
    except Exception as e:
        return f"Error retrieving asset usage: {str(e)}"

@mcp.tool()
async def analyze_image_assets(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes). Example: '9873186703'"),
    days: int = Field(default=30, description="Number of days to look back (7, 30, 90, etc.)")
) -> str:
    """
    Analyze image assets with their performance metrics across campaigns.
    
    This comprehensive tool helps you understand which image assets are performing well
    by showing metrics like impressions, clicks, and conversions for each image.
    
    RECOMMENDED WORKFLOW:
    1. First run list_accounts() to get available account IDs
    2. Then run get_account_currency() to see what currency the account uses
    3. Finally run this command to analyze image asset performance
    
    Args:
        customer_id: The Google Ads customer ID as a string (10 digits, no dashes)
        days: Number of days to look back (default: 30)
        
    Returns:
        Detailed report of image assets and their performance metrics
        
    Example:
        customer_id: "1234567890"
        days: 14
    """
    # Make sure to use a valid date range format
    # Valid formats are: LAST_7_DAYS, LAST_14_DAYS, LAST_30_DAYS, etc. (with underscores)
    if days == 7:
        date_range = "LAST_7_DAYS"
    elif days == 14:
        date_range = "LAST_14_DAYS"
    elif days == 30:
        date_range = "LAST_30_DAYS"
    else:
        # Default to 30 days if not a standard range
        date_range = "LAST_30_DAYS"
        
    query = f"""
        SELECT
            asset.id,
            asset.name,
            asset.image_asset.full_size.url,
            asset.image_asset.full_size.width_pixels,
            asset.image_asset.full_size.height_pixels,
            campaign.name,
            metrics.impressions,
            metrics.clicks,
            metrics.conversions,
            metrics.cost_micros
        FROM
            campaign_asset
        WHERE
            asset.type = 'IMAGE'
            AND segments.date DURING LAST_30_DAYS
        ORDER BY
            metrics.impressions DESC
        LIMIT 200
    """
    
    try:
        creds = get_credentials()
        headers = get_headers(creds)
        
        formatted_customer_id = format_customer_id(customer_id)
        url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{formatted_customer_id}/googleAds:search"
        
        payload = {"query": query}
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            return f"Error analyzing image assets: {response.text}"
        
        results = response.json()
        if not results.get('results'):
            return "No image asset performance data found for this customer ID and time period."
        
        # Group results by asset ID
        assets_data = {}
        for result in results.get('results', []):
            asset = result.get('asset', {})
            asset_id = asset.get('id')
            
            if asset_id not in assets_data:
                assets_data[asset_id] = {
                    'name': asset.get('name', f"Asset {asset_id}"),
                    'url': asset.get('imageAsset', {}).get('fullSize', {}).get('url', 'N/A'),
                    'dimensions': f"{asset.get('imageAsset', {}).get('fullSize', {}).get('widthPixels', 'N/A')} x {asset.get('imageAsset', {}).get('fullSize', {}).get('heightPixels', 'N/A')}",
                    'impressions': 0,
                    'clicks': 0,
                    'conversions': 0,
                    'cost_micros': 0,
                    'campaigns': set(),
                    'ad_groups': set()
                }
            
            # Aggregate metrics
            metrics = result.get('metrics', {})
            assets_data[asset_id]['impressions'] += int(metrics.get('impressions', 0))
            assets_data[asset_id]['clicks'] += int(metrics.get('clicks', 0))
            assets_data[asset_id]['conversions'] += float(metrics.get('conversions', 0))
            assets_data[asset_id]['cost_micros'] += int(metrics.get('costMicros', 0))
            
            # Add campaign and ad group info
            campaign = result.get('campaign', {})
            ad_group = result.get('adGroup', {})
            
            if campaign.get('name'):
                assets_data[asset_id]['campaigns'].add(campaign.get('name'))
            if ad_group.get('name'):
                assets_data[asset_id]['ad_groups'].add(ad_group.get('name'))
        
        # Format the results
        output_lines = [f"Image Asset Performance Analysis for Customer ID {formatted_customer_id} (Last {days} days):"]
        output_lines.append("=" * 100)
        
        # Sort assets by impressions (highest first)
        sorted_assets = sorted(assets_data.items(), key=lambda x: x[1]['impressions'], reverse=True)
        
        for asset_id, data in sorted_assets:
            output_lines.append(f"\nAsset ID: {asset_id}")
            output_lines.append(f"Name: {data['name']}")
            output_lines.append(f"Dimensions: {data['dimensions']}")
            
            # Calculate CTR if there are impressions
            ctr = (data['clicks'] / data['impressions'] * 100) if data['impressions'] > 0 else 0
            
            # Format metrics
            output_lines.append(f"\nPerformance Metrics:")
            output_lines.append(f"  Impressions: {data['impressions']:,}")
            output_lines.append(f"  Clicks: {data['clicks']:,}")
            output_lines.append(f"  CTR: {ctr:.2f}%")
            output_lines.append(f"  Conversions: {data['conversions']:.2f}")
            output_lines.append(f"  Cost (micros): {data['cost_micros']:,}")
            
            # Show where it's used
            output_lines.append(f"\nUsed in {len(data['campaigns'])} campaigns:")
            for campaign in list(data['campaigns'])[:5]:  # Show first 5 campaigns
                output_lines.append(f"  - {campaign}")
            if len(data['campaigns']) > 5:
                output_lines.append(f"  - ... and {len(data['campaigns']) - 5} more")
            
            # Add URL
            if data['url'] != 'N/A':
                output_lines.append(f"\nImage URL: {data['url']}")
            
            output_lines.append("-" * 100)
        
        return "\n".join(output_lines)
    
    except Exception as e:
        return f"Error analyzing image assets: {str(e)}"
