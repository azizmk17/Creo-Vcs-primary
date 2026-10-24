"""A request-scoped filename index, separate from saved engineering status."""
import os
import re


class WorkingDirectoryInventory:
    def __init__(self, directory, *, cancel_cb=None):
        self.directory = os.path.abspath(directory)
        self.names = set()
        self._lookup = set()
        self.latest_versions = {}
        self._external_exists = {}
        with os.scandir(self.directory) as entries:
            for entry in entries:
                if cancel_cb and cancel_cb():
                    raise InterruptedError("Directory check cancelled")
                if not entry.is_file():
                    continue
                self.names.add(entry.name)
                self._lookup.add(os.path.normcase(entry.name))
                match = re.fullmatch(r"(.+\.(?:prt|asm|drw))\.(\d+)", entry.name, re.I)
                if match:
                    base = os.path.normcase(match[1])
                    self.latest_versions[base] = max(
                        self.latest_versions.get(base, 0), int(match[2])
                    )

    def exists(self, filename):
        path = os.path.abspath(os.path.join(self.directory, filename))
        if os.path.normcase(os.path.dirname(path)) == os.path.normcase(self.directory):
            return os.path.normcase(os.path.basename(path)) in self._lookup
        if path not in self._external_exists:
            self._external_exists[path] = os.path.isfile(path)
        return self._external_exists[path]

    def latest_version(self, base):
        return self.latest_versions.get(os.path.normcase(base), 0)
