import os
import unittest
from unittest.mock import patch

os.environ["LOCAL_MODE"] = "true"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text

from app.core.config import settings
from app.db.session import _ensure_run_browser_column
from app.schemas import RunIn
from app.services.browser import (
    browser_engine,
    context_options,
    launch_browser,
    launch_options,
    normalize_browser,
)


class FakeBrowserType:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def launch(self, **options):
        self.calls.append(options)
        if self.error:
            raise self.error
        return {"options": options}


class FakePlaywright:
    def __init__(self):
        self.chromium = FakeBrowserType()
        self.firefox = FakeBrowserType()
        self.webkit = FakeBrowserType()


def patched_settings(**overrides):
    values = {
        "BROWSER_TYPE": "chromium",
        "BROWSER_HEADLESS": True,
        "BROWSER_SLOW_MO": 0,
        "BROWSER_CHANNEL": "",
        "BROWSER_EXECUTABLE": "",
        "VIEWPORT_WIDTH": 1280,
        "VIEWPORT_HEIGHT": 800,
    }
    values.update(overrides)
    return patch.multiple(settings, **values)


class BrowserSelectionTests(unittest.TestCase):
    def test_names_and_aliases(self):
        self.assertEqual(normalize_browser("edge"), "msedge")
        self.assertEqual(normalize_browser("google-chrome"), "chrome")
        self.assertEqual(normalize_browser("safari"), "webkit")
        self.assertEqual(browser_engine("msedge"), "chromium")
        self.assertEqual(browser_engine("firefox"), "firefox")
        with self.assertRaises(ValueError):
            normalize_browser("unknown")

    def test_only_chromium_receives_chromium_arguments(self):
        with patched_settings():
            self.assertIn("args", launch_options("chromium"))
            self.assertNotIn("args", launch_options("firefox"))
            self.assertNotIn("args", launch_options("webkit"))

    def test_installed_browsers_use_channels(self):
        with patched_settings():
            self.assertEqual(launch_options("chrome")["channel"], "chrome")
            self.assertEqual(launch_options("msedge")["channel"], "msedge")

    def test_context_does_not_spoof_other_engines(self):
        with patched_settings():
            self.assertIn("user_agent", context_options("chromium"))
            self.assertNotIn("user_agent", context_options("firefox"))
            self.assertNotIn("user_agent", context_options("webkit"))

    def test_run_schema_validates_browser(self):
        self.assertEqual(RunIn(inputs={}, browser="firefox").browser, "firefox")
        with self.assertRaises(ValidationError):
            RunIn(inputs={}, browser="unknown")


class BrowserLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def test_firefox_and_webkit_use_their_own_engines(self):
        playwright = FakePlaywright()
        with patched_settings():
            await launch_browser(playwright, "firefox")
            await launch_browser(playwright, "webkit")
        self.assertEqual(len(playwright.firefox.calls), 1)
        self.assertEqual(len(playwright.webkit.calls), 1)
        self.assertEqual(playwright.chromium.calls, [])

    async def test_missing_browser_error_contains_install_command(self):
        playwright = FakePlaywright()
        playwright.firefox.error = RuntimeError("executable missing")
        with patched_settings():
            with self.assertRaisesRegex(
                RuntimeError, "playwright install firefox"
            ):
                await launch_browser(playwright, "firefox")
    async def test_missing_dependencies_error_contains_deps_command(self):
        playwright = FakePlaywright()
        playwright.webkit.error = RuntimeError("Host system is missing dependencies")
        with patched_settings():
            with self.assertRaisesRegex(
                RuntimeError, "playwright install-deps webkit"
            ):
                await launch_browser(playwright, "webkit")


class BrowserMigrationTests(unittest.TestCase):
    def test_existing_runs_table_receives_browser_default(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE runs (id VARCHAR(36) PRIMARY KEY)"))
            _ensure_run_browser_column(connection)
            columns = {
                column["name"] for column in inspect(connection).get_columns("runs")
            }
            self.assertIn("browser", columns)
            connection.execute(text("INSERT INTO runs (id) VALUES ('old-run')"))
            browser = connection.execute(
                text("SELECT browser FROM runs WHERE id = 'old-run'")
            ).scalar_one()
            self.assertEqual(browser, "chromium")
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
