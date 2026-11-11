"""Which remedies to suggest for an incident, from its root cause. Pure: no DB, no HTTP.

The suggestion comes from what the system detected (the root cause), never from the simulator's running
conditions, so it is what a real farm would see.
"""

from __future__ import annotations

from app.domain.conditions import Remedy

_BY_ROOT_CAUSE: dict[str, tuple[Remedy, ...]] = {
    "pump_failure": (Remedy.REPAIR_AERATOR,),
    "do_out_of_range": (Remedy.REPAIR_AERATOR, Remedy.CUT_FEED, Remedy.THIN_STOCK),
    "nh3_out_of_range": (Remedy.CUT_FEED, Remedy.CLEAN_BOTTOM),
    "tan_out_of_range": (Remedy.CUT_FEED, Remedy.CLEAN_BOTTOM),
    "nitrite_out_of_range": (Remedy.CUT_FEED, Remedy.CLEAN_BOTTOM),
    "orp_out_of_range": (Remedy.CLEAN_BOTTOM,),
    "co2_out_of_range": (Remedy.LIME, Remedy.CUT_FEED, Remedy.THIN_STOCK),
    "alkalinity_out_of_range": (Remedy.LIME,),
    "sensor_offline": (Remedy.FIX_SENSOR,),
}


def suggested_remedies(root_cause: str | None) -> list[Remedy]:
    """Remedies that address the root cause, most likely first; empty when none of them helps."""
    return list(_BY_ROOT_CAUSE.get(root_cause or "", ()))


# Remedies a machine may apply without a person: only ones that are switched remotely and cannot harm the pond.
# A stopped aerator kills a pond within the hour, so the backup aerator is switched on at once; everything else
# (feeding, liming, siphoning) needs someone at the pond.
_AUTOMATIC: dict[str, Remedy] = {"pump_failure": Remedy.BACKUP_AERATOR}


def automatic_remedy(root_cause: str | None) -> Remedy | None:
    """The remedy the auto-remediation workflow applies for this root cause; None = leave it to the farmer."""
    return _AUTOMATIC.get(root_cause or "")
