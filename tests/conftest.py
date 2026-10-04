import pytest

from gp2chc import i18n


@pytest.fixture(autouse=True)
def french():
    """Les tests vérifient les messages en français, quelle que soit la langue du système."""
    i18n.set_language("fr")
    yield
    i18n.set_language("fr")
