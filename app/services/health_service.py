from datetime import datetime, timezone


class HealthService:
    @staticmethod
    def get_status() -> dict[str, str]:
        return {
            'status': 'ok',
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
