from typer.testing import CliRunner

from stms import __version__
from stms.cli import app


def test_import_and_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0 and "STMS" in result.output and __version__ == "0.1.0"
