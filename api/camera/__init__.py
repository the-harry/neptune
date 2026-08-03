"""WOLFANG 4K action-camera control + streaming integration.

Control plane only (REST + telemetry WS); the video plane is go2rtc (RTSP->WebRTC,
zero transcode). See README. Protocol reverse-engineered from vendor-app HAR
captures — values are ground truth, not to be "improved".
"""
