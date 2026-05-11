class PlatformAuthService:
    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}
        self._credentials: dict[str, dict[str, str]] = {}

    def set_token(self, platform: str, token: str) -> None:
        self._tokens[platform] = token.strip()

    def get_token(self, platform: str) -> str | None:
        token = self._tokens.get(platform)
        if token:
            return token
        return None

    def set_credentials(self, platform: str, credentials: dict[str, str]) -> None:
        self._credentials[platform] = {key: value.strip() for key, value in credentials.items()}

    def get_credentials(self, platform: str) -> dict[str, str] | None:
        credentials = self._credentials.get(platform)
        if credentials:
            return credentials
        return None


platform_auth_service = PlatformAuthService()
