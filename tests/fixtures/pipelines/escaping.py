"""Argo expressions and Helm values in awkward places, to prove `helm template` renders them
exactly. strings.tricky holds characters that break YAML if spliced in unescaped; Typed uses
numbers and booleans."""

from stagecraft.spec import Param, Pipeline, Stage, Step

from fixtures.actions import Shell, Typed

SCRIPT = """\
set -eu
echo "workflow: {{workflow.name}}"
echo 'param: {{ param.repo }}'
echo "expr: {{=sprig.upper(workflow.parameters.repo)}}"
echo "quoted expr: {{=workflow.parameters.repo + "-suffix"}}"
echo "backtick expr: {{=`literal`}}"
echo "shell backticks: `date +%Y`"
echo "helm-looking text that is not an expression: .Values.x"
"""

VALUES_SCRIPT = """\
echo 'tricky: {{ values.strings.tricky }}'
echo "tricky again: {{ values.strings.tricky }} {{workflow.name}}"
"""

pipeline = Pipeline(
    name="escaping",
    params=[Param("repo", default="example/repo")],
    stages=[
        Stage(
            "only",
            [
                Step("exprs", Shell(script=SCRIPT)),
                Step("values", Shell(script=VALUES_SCRIPT)),
                Step("whole", Shell(script="{{ values.strings.tricky }}")),
                Typed(),
            ],
        )
    ],
)
