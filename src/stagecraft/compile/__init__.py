"""Compiler: pure functions from a pipeline spec to Hera objects. See DESIGN.md section 7.

No I/O, no environment reads, no globals. Hera objects are constructed explicitly rather than
with Hera's context managers, which keep module-level state.
"""

import dataclasses

from hera.workflows import DAG, Parameter, Task, WorkflowTemplate

from stagecraft.compile.placeholders import lower_model
from stagecraft.errors import CompileError
from stagecraft.spec import Param, Pipeline, Stage

ENTRYPOINT = "main"


def compile_pipeline(pipeline: Pipeline) -> WorkflowTemplate:
    """Compile a pipeline to a WorkflowTemplate.

    Each stage becomes a sub-DAG template of parallel block tasks, invoked as one task in the
    entrypoint DAG. Stage N depends on stage N-1, one edge per stage.
    """
    params = {p.name for p in pipeline.params}
    templates = []
    stage_tasks = []
    previous: Stage | None = None
    for stage in pipeline.stages:
        stage_dag, block_templates = _compile_stage(pipeline, stage, params)
        templates += [stage_dag, *block_templates]
        stage_tasks.append(
            Task(
                name=stage.name,
                template=stage_dag,
                depends=previous.name if previous else None,
            )
        )
        previous = stage
    templates.insert(0, DAG(name=ENTRYPOINT, tasks=stage_tasks))
    _check_unique_template_names(pipeline, templates)

    return WorkflowTemplate(
        name=pipeline.name,
        entrypoint=ENTRYPOINT,
        arguments=[_compile_param(p) for p in pipeline.params],
        templates=templates,
    )


def _compile_param(param: Param) -> Parameter:
    # Hera serializes an explicit value=None as the string "null", so unset fields are omitted.
    fields = {"value": param.default, "enum": param.enum, "description": param.description}
    return Parameter(name=param.name, **{k: v for k, v in fields.items() if v is not None})


def _compile_stage(pipeline: Pipeline, stage: Stage, params: set[str]):
    tasks = []
    block_templates = []
    for block in stage.blocks:
        try:
            template = lower_model(block, params).compile()
        except CompileError as e:
            where = f"pipeline {pipeline.name!r}, stage {stage.name!r}, block {block.block_name!r}"
            raise CompileError(f"{where}: {e}") from e
        template = dataclasses.replace(template, name=f"{stage.name}-{block.block_name}")
        block_templates.append(template)
        tasks.append(Task(name=block.block_name, template=template))
    return DAG(name=stage.name, tasks=tasks), block_templates


def _check_unique_template_names(pipeline: Pipeline, templates) -> None:
    seen = set()
    for template in templates:
        if template.name in seen:
            raise CompileError(
                f"pipeline {pipeline.name!r}: template name {template.name!r} is generated twice; "
                f"rename a stage or block ({ENTRYPOINT!r} is reserved and stage-block names "
                "must not collide with stage names)"
            )
        seen.add(template.name)
