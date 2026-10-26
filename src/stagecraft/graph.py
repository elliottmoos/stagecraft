"""Mermaid diagram of a pipeline, for reading its shape in review or docs.

It shows the pipeline as authored (setup, source, stages, steps, conditions, statuses, handlers,
triggers), not the Argo templates it compiles to.
"""

import json

from stagecraft.spec import HANDLER_FIELDS, Pipeline, Stage, Step

_HANDLER_LABELS = {"on_success": "on success", "on_failure": "on failure", "finally_": "finally"}


def mermaid(pipeline: Pipeline) -> str:
    title = pipeline.name + (f" (status {pipeline.status})" if pipeline.status else "")
    # JSON strings are valid YAML, so any name or context is safe in the frontmatter.
    lines = ["---", f"title: {json.dumps(title)}", "---", "flowchart LR"]
    for i, stage in enumerate(pipeline.stages):
        lines.append(f'  subgraph s{i}["{_label(stage.name, stage)}"]')
        for j, step in enumerate(stage.steps):
            lines.append(f'    s{i}b{j}["{_label(step.name, step)}"]')
        lines.append("  end")
    # The run starts with the setup, then the source, then the stages.
    first = "s0"
    if pipeline.source:
        url, revision = (_escape(pipeline.source.url), _escape(pipeline.source.revision))
        lines += [f'  src[("source<br/>{url}<br/>{revision}")]', f"  src --> {first}"]
        first = "src"
    if pipeline.setup:
        setup = pipeline.setup
        label = setup.name if isinstance(setup, Step) else f"{setup.name}/{setup.template}"
        lines += [f'  setup[["setup<br/>{_escape(label)}"]]', f"  setup --> {first}"]
        first = "setup"
    lines += [f"  s{i - 1} --> s{i}" for i in range(1, len(pipeline.stages))]
    # Artifacts flow from the producing step to the step whose inputs bind them.
    ids = {
        (stage.name, step.name): f"s{i}b{j}"
        for i, stage in enumerate(pipeline.stages)
        for j, step in enumerate(stage.steps)
    }
    for i, stage in enumerate(pipeline.stages):
        for j, step in enumerate(stage.steps):
            for reference in step.inputs.values():
                stage_name, step_name, artifact = reference.split(".")
                if producer := ids.get((stage_name, step_name)):
                    lines.append(f"  {producer} -->|{_escape(artifact)}| s{i}b{j}")
    for i, stage in enumerate(pipeline.stages):
        for j, step in enumerate(stage.steps):
            lines += _handlers(f"s{i}b{j}", step, HANDLER_FIELDS)
        lines += _handlers(f"s{i}", stage, HANDLER_FIELDS)
    last = f"s{len(pipeline.stages) - 1}"
    lines += _handlers(last, pipeline, ("on_success", "on_failure"), node="p", edge="pipeline ")
    for k, trigger in enumerate(pipeline.triggers):
        schedule = " ".join(filter(None, [trigger.schedule, trigger.timezone]))
        lines.append(f'  t{k}(["{_escape(trigger.name)}<br/>{_escape(schedule)}"])')
        lines.append(f"  t{k} -.-> {first}")
    return "\n".join(lines) + "\n"


def _label(name: str, step: Stage | Step) -> str:
    parts = [name]
    if step.when:
        parts.append(f"when: {step.when}")
    if step.status:
        parts.append(f"status: {step.status}")
    return "<br/>".join(_escape(p) for p in parts)


def _handlers(
    owner_id: str,
    owner: Pipeline | Stage | Step,
    fields: tuple[str, ...],
    node: str | None = None,
    edge: str = "",
) -> list[str]:
    lines = []
    handlers = [(field, h) for field in fields for h in getattr(owner, field)]
    for k, (field, handler) in enumerate(handlers):
        handler_id = f"{node or owner_id + 'h'}{k}"
        lines.append(f'  {handler_id}["{_escape(handler.name)}"]')
        lines.append(f"  {owner_id} -.->|{edge}{_HANDLER_LABELS[field]}| {handler_id}")
    return lines


def _escape(text: str) -> str:
    # Mermaid entity codes; "#" first, since the codes themselves start with it.
    for char, code in (("#", "#35;"), ('"', "#quot;"), ("<", "#lt;"), (">", "#gt;")):
        text = text.replace(char, code)
    return text.replace("\n", "<br/>")
