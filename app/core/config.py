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
    # Dashboard id (from its URL or `python -m app.cli.datadog_push`): the incident panel links straight to it
    DD_DASHBOARD_ID: str = ""
    # Farmer alert: comma-separated addresses datadog_push adds to notify:email monitors (kept in .env only)
    FARMER_ALERT_EMAILS: str = ""
    # Where the farmer opens the control panel from the alert (a URL their phone can reach); empty = no link
    PUBLIC_BASE_URL: str = ""

    DD_AGENT_HOST: str = "localhost"
    DD_TRACE_AGENT_PORT: int = 8126
    DD_DOGSTATSD_PORT: int = 8125

    FARM_ID: str = "farm-001"
    FARM_NAME: str = "ShrimpWatch Demo Farm"
    NUM_PONDS: int = 6

    SIMULATOR_INTERVAL_SEC: int = 30
    API_URL: str = "http://localhost:8000"

    TS_TTL_SECONDS: int = 7 * 24 * 3600
    ROLLUP_TTL_SECONDS: int = 365 * 24 * 3600
    ROLLUP_INTERVAL_SEC: int = 300

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
    TAN_WARN: float = 1.0  # below the ~1.45 mg/L juvenile safe level (Zhang et al. 2019), above lab larvae/PL limits
    PH_SWING_WARN: float = 0.5
    # Warn when the straight-line DO forecast reaches DO_CRITICAL within this many minutes
    DO_FORECAST_WARN_MIN: float = 30.0

    # Worker: check interval and thresholds for detecting pump failure / recovery
    WORKER_INTERVAL_SEC: int = 30
    DO_WINDOW_SEC: int = 300
    PUMP_FAILED_MAX_WATTS: float = 50.0
    PUMP_RUNNING_MIN_WATTS: float = 500.0
    # Consecutive pump_status = 0 readings that mean the aerator has stopped (checked at ingest and by the worker)
    PUMP_STOPPED_READINGS: int = 2
    DO_RECOVERY_MARGIN: float = 0.5
    # Detection SLO: a critical incident opens within this many seconds of the fault
    DETECTION_SLO_SEC: int = 120
    # How far back an injected fault (aerator failure, condition, scenario) can explain a new incident
    FAULT_LOOKBACK_SEC: int = 2 * 3600


settings = Settings()
