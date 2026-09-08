"""occbridge — host-side occupancy bridge for an STM32N6 edge vision sensor.

Reads the firmware's per-frame ``DET`` wire protocol off a serial line (or a
replay log), debounces the raw person count into stable occupancy events, and
emits schema-v0 event envelopes to an event spine.

The pure-logic layers (:mod:`occbridge.protocol`, :mod:`occbridge.aggregator`,
:mod:`occbridge.events`) perform no I/O and carry the bulk of the test suite.
"""

__version__ = "0.1.0"
SCHEMA_VERSION = 0
