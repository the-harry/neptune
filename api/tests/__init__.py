"""API test suite (stdlib unittest — see run.py).

A package rather than a bare folder of scripts so `tests.test_x` is importable with
api/ as the top level, which is the same import root the api itself runs under
(`from config import settings`, `from nav.x import y`). Without that the tests would
need their own path juggling and would stop matching how the code is actually loaded.
"""
