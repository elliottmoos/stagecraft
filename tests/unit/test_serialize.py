import pytest
from hera.workflows import Container, WorkflowTemplate
from hera.workflows.models import EnvVar

from stagecraft.errors import SerializeError
from stagecraft.serialize import serialize


def workflow(container: Container) -> WorkflowTemplate:
    return WorkflowTemplate(name="w", entrypoint="c", templates=[container])


def test_value_references_cannot_be_mapping_keys():
    container = Container(name="c", image="i", node_selector={"<<values:a.b>>": "x"})

    with pytest.raises(SerializeError, match=r"value reference in a mapping key '<<values:a\.b>>'"):
        serialize(workflow(container))


def test_text_that_looks_like_an_internal_token_is_rejected():
    container = Container(name="c", image="i", env=[EnvVar(name="X", value="STAGECRAFTVALUE0WX")])

    with pytest.raises(SerializeError, match="reserved"):
        serialize(workflow(container))
