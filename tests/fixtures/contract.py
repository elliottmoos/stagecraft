"""The fixture chart's values contract, as the CompileContext every compile test uses."""

from pathlib import Path

from stagecraft.context import CompileContext
from stagecraft.contract import ValuesContract

SCHEMA_PATH = Path(__file__).parent / "chart" / "values.schema.json"
CONTEXT = CompileContext(ValuesContract.load(SCHEMA_PATH))
