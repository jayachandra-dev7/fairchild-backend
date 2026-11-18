from __future__ import annotations

from app.db import automation_store
from app.schemas.automation import AutomationSettings, BlogAutomationSettings, ProductAutomationSettings


def load_all() -> AutomationSettings:
    return AutomationSettings(
        product=ProductAutomationSettings(**(automation_store.load_settings('product') or {})),
        blog=BlogAutomationSettings(**(automation_store.load_settings('blog') or {})),
    )


def save_all(settings: AutomationSettings) -> AutomationSettings:
    automation_store.save_settings('product', settings.product.model_dump())
    automation_store.save_settings('blog', settings.blog.model_dump())
    return settings
