import pytest
import app_logging


@pytest.fixture(autouse=True)
def log_directory(tmp_path):
    """Keep synthetic requests out of the user's real troubleshooting history."""
    directory = tmp_path / 'logs'
    app_logging.configure_logs(directory)
    yield directory
    app_logging.configure_logs()
