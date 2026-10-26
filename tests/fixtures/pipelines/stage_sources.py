"""A stage source: the e2e stage checks out octocat/Spoon-Knife beside the pipeline's
Hello-World. Its steps fail unless both checkouts are where they should be, at the right
commits; the later stage fails if it can see the e2e stage's source."""

from stagecraft.spec import Pipeline, Source, Stage

from fixtures.actions import Shell

pipeline = Pipeline(
    name="stage-sources-ci",
    source=Source(url="https://github.com/octocat/Hello-World.git", revision="master"),
    stages=[
        Stage(
            "e2e",
            [
                Shell(
                    script='test -f README && cd "$STAGECRAFT_SOURCE_SPOON" && test -f index.html'
                    ' && test "$(cat .git/HEAD)" = "{{ source.spoon.sha }}"'
                )
            ],
            sources=[
                Source(
                    name="spoon", url="https://github.com/octocat/Spoon-Knife.git", revision="main"
                )
            ],
        ),
        Stage("after", [Shell(script='test -z "${STAGECRAFT_SOURCE_SPOON:-}" && test -f README')]),
    ],
)
