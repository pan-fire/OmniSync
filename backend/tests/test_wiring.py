"""Every router's services are wired by one helper, used by main.py and test_client.

A router that grows a ``set_*`` function must be wired in
backend/api/wiring.py, or it would run with None in production or in tests.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from dataclasses import fields

import pytest

import backend.api.routes as routes_pkg
from backend.api import wiring
from backend.api.wiring import RouteServices, unwire_routes, wire_routes


def _setters() -> list[tuple[str, str]]:
    found = []
    for info in pkgutil.iter_modules(routes_pkg.__path__):
        module = importlib.import_module(f"{routes_pkg.__name__}.{info.name}")
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("set_") and fn.__module__ == module.__name__:
                found.append((module.__name__, name))
    return sorted(found)


def test_all_route_setters_are_found() -> None:
    # Guards the discovery itself: main.py wired 17 when this was written.
    assert len(_setters()) >= 17


def test_wire_routes_calls_every_setter_with_a_service(monkeypatch: pytest.MonkeyPatch) -> None:
    setters = _setters()
    calls: dict[tuple[str, str], object] = {}
    for module_name, name in setters:
        module = importlib.import_module(module_name)

        def record(value, _key=(module_name, name)):
            calls[_key] = value

        monkeypatch.setattr(module, name, record)

    services = RouteServices(**{f.name: object() for f in fields(RouteServices)})
    wire_routes(services)

    assert sorted(calls) == setters
    assert all(value is not None for value in calls.values())


def test_unwire_routes_clears_every_setter(monkeypatch: pytest.MonkeyPatch) -> None:
    setters = _setters()
    calls: dict[tuple[str, str], object] = {}
    for module_name, name in setters:
        module = importlib.import_module(module_name)
        monkeypatch.setattr(module, name, lambda value, _key=(module_name, name): calls.__setitem__(_key, value))

    unwire_routes()

    assert sorted(calls) == setters
    assert all(value is None for value in calls.values())


def test_main_wires_through_the_helper() -> None:
    import backend.main as main

    source = inspect.getsource(main.lifespan)
    assert "wire_routes(" in source
    assert ".set_" not in source, "main.py must wire routers through wire_routes()"
    assert wiring.wire_routes is main.wire_routes


async def test_test_client_wires_every_router(test_client, test_services) -> None:
    from backend.api.routes import backups, browse, config, notifications, remotes, wizard

    assert backups._backup_service is test_services.backup_service
    assert backups._db_factory is test_services.db_factory
    assert remotes._db_factory is test_services.db_factory
    assert notifications._dispatcher is test_services.dispatcher
    assert notifications._webpush_channel is test_services.webpush_channel
    assert browse._rclone_service is test_services.rclone
    assert wizard._rclone_service is test_services.rclone
    assert config._rclone_service is test_services.rclone
