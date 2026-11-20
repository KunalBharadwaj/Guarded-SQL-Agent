"""Report metadata must name the concrete backend, not the literal 'auto'."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.run_eval import resolve_provider_name


def test_oracle_and_stub_pass_through():
    assert resolve_provider_name("oracle") == "oracle"
    assert resolve_provider_name("stub") == "stub"


def test_auto_resolves_to_the_concrete_backend():
    # Regression: 'auto' must be mapped to the real client's name, not left as 'auto'.
    fake = lambda: types.SimpleNamespace(name="groq")
    assert resolve_provider_name("auto", client_factory=fake) == "groq"
