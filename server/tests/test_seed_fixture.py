"""The cross-language drift guard.

client/src/test/seed.fixture.ts is generated from the real editor and is the
source of truth for the normalised seed; seedRoundTrip.test.ts proves TipTap
emits it. This asserts data.py carries the same bytes, so an edit on either side
fails loudly.
"""

import json
import re
from pathlib import Path

import pytest

from app.data import SEED_DOCUMENTS

FIXTURE = Path(__file__).parents[2] / "client/src/test/seed.fixture.ts"


@pytest.mark.parametrize(
    ("index", "name"),
    [(0, "SEED_1"), (1, "SEED_2")],
)
def test_seed_matches_client_fixture(index: int, name: str) -> None:
    if not FIXTURE.exists():
        pytest.skip("client fixture not present (client/ not installed)")

    match = re.search(rf'^export const {name} = (".*");$', FIXTURE.read_text(), re.M)
    assert match is not None, f"{name} not found in {FIXTURE}"
    assert json.loads(match.group(1)) == SEED_DOCUMENTS[index].content
