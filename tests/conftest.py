from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = REPO_ROOT / "samples"


@pytest.fixture
def samples_dir() -> Path:
    return SAMPLES_DIR


@pytest.fixture
def detail_samples_dir(samples_dir: Path) -> Path:
    return samples_dir / "detail"
