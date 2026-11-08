"""Names vulture reports as unused but that are used outside Python call sites.

Each entry says why it is kept. Add a name here only when it is really used (by a framework, by
serialization, by the environment); otherwise delete the dead code.
"""

from app.core.config import Settings
from app.core.log import TraceIdFilter
from app.domain.models import IncidentEvidence

# Settings read from the environment / .env: documented config surface, also set in docker-compose.yml.
Settings.DD_TRACE_AGENT_PORT
Settings.FARM_NAME
# Thresholds for manual test-kit metrics, shown as markers on the Datadog dashboard.
Settings.ALK_WARN
Settings.NITRITE_WARN

# Picked up by the JSON log formatter as a log field.
TraceIdFilter.service

# Pydantic fields: written to incidents.evidence in MongoDB and read by the fingerprint features.
IncidentEvidence.do_drop_rate_mg_per_l_per_min
IncidentEvidence.avg_pump_power_watts
IncidentEvidence.last_orp_mv
IncidentEvidence.ph_swing_24h
IncidentEvidence.do_window_sec
IncidentEvidence.notes
