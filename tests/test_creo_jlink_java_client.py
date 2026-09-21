import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from core.integrations.creo_bridge import NexusCreoBridge


ROOT = Path(__file__).resolve().parents[1]
JLINK_ROOT = ROOT / "integrations" / "creo_jlink"
JDK = Path(r"C:\Program Files\Java\jdk1.7.0_80")
JAVA = Path(r"C:\Program Files\Java\jre7\bin\java.exe")


class _JavaSmokeController:
    def dispatch(self, method, path, _query, body):
        if method == "GET" and path == "/api/v1/context":
            return {
                "api_version": 1,
                "logged_in": True,
                "user": {"id": 7, "username": "smoke-user"},
                "project": {"id": 9, "name": "Smoke project", "version_label": "A"},
            }
        if method == "GET" and path == "/api/v1/workspaces":
            return {"workspaces": [{"id": "one", "name": "Existing"}]}
        if method == "POST" and path == "/api/v1/workspaces":
            return {"workspace": {"id": "created", "name": body.get("name")}}
        raise AssertionError(f"Unexpected smoke-test request: {method} {path}")


@unittest.skipUnless(
    (JDK / "bin" / "javac.exe").is_file() and JAVA.is_file(),
    "The Creo integration Java 7 runtime is not installed.",
)
class CreoJLinkJavaClientTests(unittest.TestCase):
    def test_mini_json_treats_optional_null_fields_as_empty_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            classes = temporary_path / "classes"
            classes.mkdir()
            sources = [
                JLINK_ROOT / "src" / "MiniJson.java",
                JLINK_ROOT / "test" / "MiniJsonOptionalSmoke.java",
            ]
            subprocess.run(
                [
                    str(JDK / "bin" / "javac.exe"),
                    "-source",
                    "1.7",
                    "-target",
                    "1.7",
                    "-Xlint:all",
                    "-d",
                    str(classes),
                    *(str(source) for source in sources),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                [str(JAVA), "-cp", str(classes), "MiniJsonOptionalSmoke"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )

        self.assertIn("MINI_JSON_OPTIONAL_OK", result.stdout)

    def test_java_7_client_calls_authenticated_bridge_get_and_post(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            connection_file = temporary_path / "bridge.json"
            classes = temporary_path / "classes"
            classes.mkdir()
            sources = [
                JLINK_ROOT / "src" / "MiniJson.java",
                JLINK_ROOT / "src" / "NexusApiException.java",
                JLINK_ROOT / "src" / "NexusApiClient.java",
                JLINK_ROOT / "test" / "NexusApiClientSmoke.java",
            ]
            subprocess.run(
                [
                    str(JDK / "bin" / "javac.exe"),
                    "-source",
                    "1.7",
                    "-target",
                    "1.7",
                    "-Xlint:all",
                    "-d",
                    str(classes),
                    *(str(source) for source in sources),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            bridge = NexusCreoBridge(
                controller=_JavaSmokeController(),
                port=0,
                connection_file=connection_file,
            )
            bridge.start()
            self.addCleanup(bridge.stop)
            environment = dict(os.environ)
            environment["NEXUS_CREO_BRIDGE_FILE"] = str(connection_file)
            result = subprocess.run(
                [str(JAVA), "-cp", str(classes), "NexusApiClientSmoke"],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
                timeout=15,
            )

        self.assertIn("JAVA_BRIDGE_SMOKE_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
