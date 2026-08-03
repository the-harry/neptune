"""ROV navigation & map subsystem.

Position from heading + a speed model, integrated ONCE (never double-integrate
accelerometer data — see build spec §2.2). Depth is measured, never estimated.
Error is linear in distance travelled (~5-15%), which is the accuracy target.
"""
