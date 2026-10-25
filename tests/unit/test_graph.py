from stagecraft.graph import mermaid
from stagecraft.spec import Param, Pipeline, Source, Stage, Step, Trigger

from fixtures.actions import Shell


def test_stages_are_ordered_subgraphs_of_steps():
    pipeline = Pipeline(
        name="p",
        stages=[
            Stage("prepare", [Step("clone", Shell(script=""))]),
            Stage("verify", [Step("lint", Shell(script="")), Step("tests", Shell(script=""))]),
        ],
    )

    assert mermaid(pipeline) == (
        "---\n"
        'title: "p"\n'
        "---\n"
        "flowchart LR\n"
        '  subgraph s0["prepare"]\n'
        '    s0b0["clone"]\n'
        "  end\n"
        '  subgraph s1["verify"]\n'
        '    s1b0["lint"]\n'
        '    s1b1["tests"]\n'
        "  end\n"
        "  s0 --> s1\n"
    )


def test_labels_show_when_and_status():
    pipeline = Pipeline(
        name="p",
        params=[
            Param("deploy", default="false"),
        ],
        source=Source(url="https://example.com/repo.git", revision="main"),
        status="ci/p",
        status_reporter=Shell(script=""),
        stages=[
            Stage(
                "deploy",
                [Step("apply", Shell(script=""), status="ci/apply")],
                when="{{ param.deploy }} == 'true'",
            )
        ],
    )

    lines = mermaid(pipeline).splitlines()

    assert lines[1] == 'title: "p (status ci/p)"'
    assert "  subgraph s0[\"deploy<br/>when: {{ param.deploy }} == 'true'\"]" in lines, lines
    assert '    s0b0["apply<br/>status: ci/apply"]' in lines


def test_handlers_hang_off_their_step_with_dotted_edges():
    alert = Step("alert", Shell(script=""))
    pipeline = Pipeline(
        name="p",
        stages=[
            Stage(
                "verify",
                [Step("tests", Shell(script=""), finally_=[Step("upload", Shell(script=""))])],
                on_failure=[alert],
            ),
            Stage("package", [Step("build", Shell(script=""))]),
        ],
        on_success=[Step("notify", Shell(script=""))],
    )

    assert mermaid(pipeline).splitlines()[-7:] == [
        "  s0 --> s1",
        '  s0b0h0["upload"]',
        "  s0b0 -.->|finally| s0b0h0",
        '  s0h0["alert"]',
        "  s0 -.->|on failure| s0h0",
        # Pipeline handlers hang off the last stage.
        '  p0["notify"]',
        "  s1 -.->|pipeline on success| p0",
    ]


def test_label_text_is_escaped():
    pipeline = Pipeline(
        name="p",
        params=[Param("x", default="")],
        stages=[Stage("s", [Shell(script="")], when='"{{ param.x }}" < "#a"')],
    )

    assert '  subgraph s0["s<br/>when: #quot;{{ param.x }}#quot; #lt; #quot;#35;a#quot;"]' in (
        mermaid(pipeline).splitlines()
    )


def test_triggers_point_at_the_first_stage():
    pipeline = Pipeline(
        name="p",
        stages=[Stage("s", [Shell(script="")])],
        triggers=[Trigger.cron("0 2 * * *", name="nightly", timezone="UTC")],
    )

    assert mermaid(pipeline).splitlines()[-2:] == [
        '  t0(["nightly<br/>0 2 * * * UTC"])',
        "  t0 -.-> s0",
    ]


def test_the_source_is_checked_out_before_the_first_stage():
    pipeline = Pipeline(
        name="p",
        source=Source(url="https://example.com/repo.git", revision="{{ param.rev }}"),
        params=[Param("rev", default="main")],
        stages=[Stage("s", [Shell(script="")])],
        triggers=[Trigger.cron("@daily")],
    )

    lines = mermaid(pipeline).splitlines()

    assert '  src[("source<br/>https://example.com/repo.git<br/>{{ param.rev }}")]' in lines
    assert "  src --> s0" in lines
    assert "  t0 -.-> src" in lines
