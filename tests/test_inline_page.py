"""InlinePage pagination maths (pure, no DB)."""

import pytest

from sqladmin_inline.inline import InlinePage


def page(count: int, page: int = 1, size: int = 3) -> InlinePage:
    return InlinePage(rows=[], page=page, page_size=size, count=count)


@pytest.mark.parametrize(
    ("count", "expected"), [(0, 1), (1, 1), (3, 1), (7, 3), (9, 3)]
)
def test_total_pages(count: int, expected: int) -> None:
    assert page(count).total_pages == expected


def test_previous_next() -> None:
    assert page(10, 1).has_previous is False
    assert page(10, 2).has_previous is True
    assert page(10, 2).has_next is True
    assert page(10, 4).has_next is False


def test_indexes_and_remaining() -> None:
    p = page(10, 2)
    assert (p.first_index, p.last_index, p.remaining) == (4, 6, 4)
    last = page(10, 4)
    assert (last.first_index, last.last_index, last.remaining) == (10, 10, 0)
    empty = page(0)
    assert (empty.first_index, empty.last_index, empty.remaining) == (0, 0, 0)


def test_page_range_small() -> None:
    assert page(9, 1).page_range == [1, 2, 3]
    assert page(2, 1, size=10).page_range == [1]


def test_page_range_with_ellipsis() -> None:
    assert InlinePage([], 10, 1, 20).page_range == [1, None, 8, 9, 10, 11, 12, None, 20]


@pytest.mark.parametrize("cur", range(1, 21))
def test_page_range_invariants(cur: int) -> None:
    r = InlinePage([], cur, 1, 20).page_range
    nums = [x for x in r if x is not None]
    assert nums == sorted(set(nums))
    assert {1, 20, cur} <= set(nums)
    assert r[0] is not None and r[-1] is not None
    for i, v in enumerate(r):
        if v is None:
            assert r[i + 1] - r[i - 1] > 1
