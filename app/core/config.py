from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    APP_NAME: str = 'Affiliate Automation Backend'
    APP_VERSION: str = '0.1.0'
    API_V1_PREFIX: str = '/api/v1'
    FRONTEND_LOCAL_ORIGIN: str = 'http://localhost:3000'
    FRONTEND_PROD_ORIGIN: str = 'https://app.domain.com'
    FRONTEND_ORIGINS: str = ''

    CJ_TOKEN: str = ''

    IMPACT_ACCOUNT_SID: str = ''
    IMPACT_AUTH_TOKEN: str = ''

    WORDPRESS_DOMAIN: str = ''
    WORDPRESS_WC_CONSUMER_KEY: str = ''
    WORDPRESS_WC_CONSUMER_SECRET: str = ''

    METRICOOL_TOKEN: str = ''
    METRICOOL_USER_ID: str = ''
    METRICOOL_BLOG_ID: str = ''

    RENDERFORM_API_KEY: str = ''
    CLAUDE_API_KEY: str = ''

    # true = render branded images with the RenderForm API, false = free local Pillow renderer.
    USE_RENDERFORM: bool = True

    # CJ account identifiers used by the unattended automation (same values the frontend sends).
    CJ_COMPANY_ID: str = '6947255'
    CJ_PID: str = '101105481'

    # Claude model(s) used by the automation review/copy/blog steps (comma separated, tried in order).
    AUTOMATION_CLAUDE_MODELS: str = 'claude-haiku-5-5'


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_cors_origins(settings: Settings | None = None) -> list[str]:
    resolved_settings = settings or get_settings()

    def normalize(raw_value: str) -> list[str]:
        origins: list[str] = []
        for origin in raw_value.split(','):
            cleaned = origin.strip()
            if cleaned and cleaned not in origins:
                origins.append(cleaned)
        return origins

    if resolved_settings.FRONTEND_ORIGINS.strip():
        return normalize(resolved_settings.FRONTEND_ORIGINS)

    origins = normalize(resolved_settings.FRONTEND_LOCAL_ORIGIN)
    for origin in normalize(resolved_settings.FRONTEND_PROD_ORIGIN):
        if origin not in origins:
            origins.append(origin)
    return origins
