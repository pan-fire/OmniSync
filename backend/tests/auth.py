"""API credentials the test suite uses.

conftest.py puts these into the environment before the app is imported, so
tests exercise the real token check and host guard rather than a stub.
"""

TEST_API_TOKEN = "test-token"
TEST_HOST = "test"  # httpx's base_url="http://test"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_API_TOKEN}"}
