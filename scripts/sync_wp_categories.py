"""Pull the WooCommerce category tree into app/data/wp_categories.json.

Usage (from fairchild-backend/): python -m scripts.sync_wp_categories
"""
import asyncio

from app.core.config import get_settings
from app.services.automation.categories import SNAPSHOT_PATH, sync_snapshot
from app.services.platform_auth_service import platform_auth_service


async def main() -> None:
    s = get_settings()
    platform_auth_service.set_credentials(
        'wordpress',
        {
            'domain': s.WORDPRESS_DOMAIN,
            'wc_consumer_key': s.WORDPRESS_WC_CONSUMER_KEY,
            'wc_consumer_secret': s.WORDPRESS_WC_CONSUMER_SECRET,
        },
    )
    snap = await sync_snapshot(to_repo=True)
    print(f"Saved {snap['total']} categories ({snap['leaf_count']} leaves) to {SNAPSHOT_PATH}")


if __name__ == '__main__':
    asyncio.run(main())
