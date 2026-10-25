"""Setup steps. setup-ci parses a GitHub push payload with an existing WorkflowTemplate
(tests/fixtures/templates/parse-webhook.yaml) and checks out the commit it names; setup-action-ci
uses an action and branches on its outputs."""

from stagecraft.spec import Param, Pipeline, Source, Stage, Step, TemplateRef

from fixtures.actions import Emit, Shell

# A trimmed GitHub push payload: Hello-World at a commit that isn't a branch tip.
PAYLOAD = (
    '{"repository": {"clone_url": "https://github.com/octocat/Hello-World.git"},'
    ' "after": "762941318ee16e59dabbacb1b4049eec22f0d303"}'
)

pipeline = Pipeline(
    name="setup-ci",
    params=[Param("payload", default=PAYLOAD, description="The GitHub webhook payload")],
    setup=TemplateRef(
        "stagecraft-parse-webhook",
        template="parse",
        arguments={"payload": "{{ param.payload }}"},
        outputs=["repo_url", "sha"],
    ),
    source=Source(url="{{ setup.repo_url }}", revision="{{ setup.sha }}"),
    stages=[
        Stage(
            "verify", [Step("head", Shell(script='test "$(cat .git/HEAD)" = "{{ setup.sha }}"'))]
        ),
    ],
)

action_pipeline = Pipeline(
    name="setup-action-ci",
    setup=Step("parse", Emit(values={"event": "push", "ref": "main"})),
    stages=[
        Stage("check", [Shell(script='test "{{ setup.ref }}" = main')]),
        Stage(
            "deploy",
            [Shell(script="true")],
            when="{{ setup.event }} == 'push'",
            finally_=[
                Shell(script='test "{{ setup.event }} {{ step.outcome }}" = "push succeeded"')
            ],
        ),
        # Fails if it runs: the condition must be false.
        Stage("release", [Shell(script="false")], when="{{ setup.event }} == 'tag'"),
    ],
)
