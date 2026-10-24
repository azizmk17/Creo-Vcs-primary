"""Repositories shared within one service graph, never a global workspace cache."""
from threading import RLock

from config import DB_NAME


class ServiceResources:
    def __init__(self, db_name=DB_NAME, repositories=None):
        self.db_name = db_name
        self._repositories = dict(repositories or {})
        self._lock = RLock()

    def repository(self, repository_type):
        with self._lock:
            if repository_type not in self._repositories:
                self._repositories[repository_type] = repository_type(db_name=self.db_name)
            return self._repositories[repository_type]


class DeferredService:
    """Construct an optional service on first use; allow normal test injection."""
    def __init__(self, factory):
        self.factory = factory

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        if self.name not in instance.__dict__:
            instance.__dict__[self.name] = self.factory(instance)
        return instance.__dict__[self.name]

    def __set__(self, instance, value):
        instance.__dict__[self.name] = value
