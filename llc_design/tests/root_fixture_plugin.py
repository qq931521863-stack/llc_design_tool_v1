"""Explicit child-pytest bridge to source-only archived root fixtures."""
from llc_design.tests.desktop_support import load_root_test_fixtures, record_test_fixture_proof

_fixtures = load_root_test_fixtures()
desktop_update_boundary = _fixtures.desktop_update_boundary
dispose_test_owned_desktop_widgets = _fixtures.dispose_test_owned_desktop_widgets


def pytest_sessionfinish(session, exitstatus):
    record_test_fixture_proof(_fixtures)
