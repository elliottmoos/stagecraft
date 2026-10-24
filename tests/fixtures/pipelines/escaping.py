"""Argo expressions in awkward places, to prove they survive `helm template` unchanged."""

from stagecraft.spec import Param, Pipeline, Stage

from fixtures.blocks import Shell

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

pipeline = Pipeline(
    name="escaping",
    params=[Param("repo", default="example/repo")],
    stages=[Stage("only", [Shell(name="exprs", script=SCRIPT)])],
)
