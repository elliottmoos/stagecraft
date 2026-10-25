"""A pipeline source: octocat/Hello-World, a tiny public repository whose history never changes.
Every step gets the checkout at /sources/main; the status reporter and handlers get the resolved
commit."""

from stagecraft.spec import Param, Pipeline, Source, Stage, Step

from fixtures.actions import Shell

pipeline = Pipeline(
    name="source-ci",
    params=[Param("revision", default="master", description="Branch, tag, or commit")],
    source=Source(
        url="https://github.com/octocat/Hello-World.git", revision="{{ param.revision }}"
    ),
    status="ci/pipeline",
    status_reporter=Shell(
        script='echo "{{ status.context }}={{ status.state }}'
        ' for {{ source.url }}@{{ source.sha }}"'
    ),
    stages=[
        Stage(
            "verify",
            [
                Step(
                    "readme",
                    Shell(
                        script='test "$PWD" = "$STAGECRAFT_SOURCE_MAIN" && grep -q Hello README'
                        ' && test "$(cat .git/HEAD)" = "{{ source.sha }}"'
                    ),
                    status="ci/readme",
                ),
            ],
            finally_=[
                Shell(script='echo "{{ step.name }} {{ step.outcome }} at {{ source.sha }}"')
            ],
        ),
    ],
)
