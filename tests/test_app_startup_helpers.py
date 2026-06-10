import pytest

from services.app_startup import build_and_include_router, include_router_checked


class FakeApp:
    def __init__(self, fail=False):
        self.fail = fail
        self.routers = []

    def include_router(self, router):
        if self.fail:
            raise ValueError("boom")
        self.routers.append(router)


def test_include_router_checked_returns_router():
    app = FakeApp()
    router = object()

    assert include_router_checked(app, router, "Test") is router
    assert app.routers == [router]


def test_build_and_include_router_labels_factory_failures():
    with pytest.raises(RuntimeError, match="Failed to build Test routes"):
        build_and_include_router(FakeApp(), "Test", lambda: (_ for _ in ()).throw(ValueError("bad")))


def test_include_router_checked_labels_include_failures():
    with pytest.raises(RuntimeError, match="Failed to register Test routes"):
        include_router_checked(FakeApp(fail=True), object(), "Test")
