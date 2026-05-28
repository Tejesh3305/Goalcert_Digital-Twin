"""NextXR Change Log — the append-only, hash-chained mutation ledger.

Every mutation that passes the Graph Writer emits exactly one Change Log
event. Events are chained per tenant (each event's hash folds in the
previous event's hash), so altering any historical event breaks the chain
from that point forward — tamper-evidence without a full WORM ledger.
"""

from .service import ChangeLog, Event, ulid

__all__ = ["ChangeLog", "Event", "ulid"]
