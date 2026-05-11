from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    APP_NAME: str = 'Affiliate Automation Backend'
    APP_VERSION: str = '0.1.0'
    API_V1_PREFIX: str = '/api/v1'
    FRONTEND_LOCAL_ORIGIN: str = 'http://localhost:3000'
    FRONTEND_PROD_ORIGIN: str = 'https://app.domain.com'


@lru_cache
def get_settings() -> Settings:
    return Settings()
