"""The setup step: runs first, as an action or a template of an existing WorkflowTemplate, and
publishes its output parameters for the whole run. See DESIGN.md section 16.

The setup runs inside a generated `steps` template whose outputs re-publish each output parameter
with a `globalName`, so `{{ setup.<output> }}` lowers to a global output readable from every
stage, hook, and `onExit`. Verified on Argo 4.1.6: a `steps` template's outputs become global
outputs, while a `dag` template's outputs with a `globalName` never do.
"""

from hera.workflows import Parameter, Steps
from hera.workflows import Step as HeraStep
from hera.workflows.models import TemplateRef as TemplateRefModel
from hera.workflows.models import ValueFrom

TASK = "setup"


def reference(output: str) -> str:
    """The Argo expression for a setup output, readable everywhere."""
    return f"{{{{workflow.outputs.parameters.{_global(output)}}}}}"


def template_ref_step(
    name: str, workflow_template: str, template: str, arguments: dict[str, str]
) -> HeraStep:
    """The step calling `template` of WorkflowTemplate `workflow_template` with `arguments`
    (already lowered)."""
    return HeraStep(
        name=name,
        template_ref=TemplateRefModel(name=workflow_template, template=template),
        arguments=[Parameter(name=k, value=v) for k, v in arguments.items()] or None,
    )


def wrapper(step: HeraStep, outputs: list[str]) -> Steps:
    """The `setup` template: runs `step` and re-publishes its `outputs` globally."""
    return Steps(
        name=TASK,
        sub_steps=[step],
        outputs=[
            Parameter(
                name=output,
                global_name=_global(output),
                value_from=ValueFrom(
                    parameter=f"{{{{steps.{step.name}.outputs.parameters.{output}}}}}"
                ),
            )
            for output in outputs
        ]
        or None,
    )


def _global(output: str) -> str:
    return f"stagecraft-setup-{output}"


def action_step(name: str, template: str) -> HeraStep:
    """The step running the setup action's compiled template, named `template`."""
    return HeraStep(name=name, template=template)
