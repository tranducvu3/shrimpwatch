from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DB: str = "shrimpwatch"

    DD_API_KEY: str = ""
    DD_APP_KEY: str = ""
    DD_SITE: str = "datadoghq.com"
    DD_ENV: str = "development"
    DD_SERVICE: str = "shrimpwatch"

    DD_AGENT_HOST: str = "localhost"
    DD_TRACE_AGENT_PORT: int = 8126
    DD_DOGSTATSD_PORT: int = 8125

    FARM_ID: str = "farm-001"
    FARM_NAME: str = "ShrimpWatch Demo Farm"
    NUM_PONDS: int = 6

    SIMULATOR_INTERVAL_SEC: int = 30
    API_URL: str = "http://localhost:8000"

    TS_TTL_SECONDS: int = 7 * 24 * 3600

    TEMP_WARN_LOW: float = 26.0
    TEMP_WARN_HIGH: float = 32.0
    DO_WARN: float = 4.0
    DO_CRITICAL: float = 3.0
    PH_WARN_LOW: float = 7.5
    PH_WARN_HIGH: float = 8.5
    ALK_WARN: float = 120.0
    CO2_WARN: float = 10.0
    DO_DROP_RATE_WARN_MGPERL_PER_MIN: float = 0.05
    SALINITY_WARN_LOW: float = 10.0
    ORP_WARN_LOW: float = 100.0
    NH3_WARN: float = 0.1
    NITRITE_WARN: float = 1.0
    PH_SWING_WARN: float = 0.5


settings = Settings()
