"""``FakeUnits`` keeps the checks of ``UnitOfWorkFactory.open`` that dashboard tests rely on."""

import threading

import pytest

from tests.fakes.units import FakeUnits


def test_open_records_each_unit_and_its_thread() -> None:
    units = FakeUnits()

    with units.open(read_only=True) as unit:
        assert units.open_now == [unit]
    with units.open():
        pass

    assert units.opened == [(True, threading.get_ident()), (False, threading.get_ident())]
    assert units.open_now == []


@pytest.mark.parametrize("value", [1, "yes", None])
def test_open_takes_read_only_as_a_bool(value: object) -> None:
    with pytest.raises(TypeError, match="read_only takes a bool"):
        with FakeUnits().open(read_only=value):  # type: ignore[arg-type]
            pass


@pytest.mark.parametrize("inner_read_only", [False, True])
def test_a_second_unit_on_the_same_thread_is_refused(inner_read_only: bool) -> None:
    units = FakeUnits()

    with units.open(read_only=True):
        with pytest.raises(RuntimeError, match="already open"):
            with units.open(read_only=inner_read_only):
                pass

    assert len(units.opened) == 1


def test_another_thread_may_open_its_own_unit() -> None:
    units = FakeUnits()
    errors: list[BaseException] = []

    def other() -> None:
        try:
            with units.open(read_only=True):
                pass
        except BaseException as exc:  # noqa: BLE001 - handed back to the test
            errors.append(exc)

    with units.open(read_only=True):
        thread = threading.Thread(target=other)
        thread.start()
        thread.join(timeout=30)

    assert errors == []
    assert len(units.opened) == 2
