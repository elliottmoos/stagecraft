"""Argo expressions and Helm values in awkward places, to prove `helm template` renders them
exactly. strings.tricky holds characters that break YAML if spliced in unescaped."""

from stagecraft.spec import Param, Pipeline, Stage, Step

from fixtures.actions import Shell

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
echo "retries={{ values.strings.retries }} debug={{ values.strings.debug }} {{workflow.name}}"
echo "big={{ values.strings.big }}"
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
                Step("whole-number", Shell(script="{{ values.strings.big }}")),
            ],
        )
    ],
)
