"""Data-source layer.

Every adapter implements a small protocol and degrades gracefully to
deterministic *synthetic* data when offline or when no API key is present, so
the whole pipeline runs end-to-end with zero credentials for development/tests.
"""
