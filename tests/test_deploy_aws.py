"""The AWS template and bootstrap script: they parse, and the properties that keep the node private stay true. No AWS."""

import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
AWS = ROOT / "deploy" / "aws"


class _Loader(yaml.SafeLoader):
    """Reads CloudFormation's short tags (!Ref, !Sub, ...) as plain data."""


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node, deep=True)}
    return {suffix: loader.construct_mapping(node, deep=True)}


_Loader.add_multi_constructor("!", _tag)


@pytest.fixture(scope="module")
def template():
    return yaml.load((AWS / "cipherops-node.yaml").read_text(encoding="utf-8"), Loader=_Loader)


def test_the_bootstrap_script_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(AWS / "bootstrap.sh")]).returncode == 0


def test_one_instance_runs_both_tracks_and_signals_the_stack(template):
    instances = [r for r in template["Resources"].values() if r["Type"] == "AWS::EC2::Instance"]
    assert len(instances) == 1
    assert template["Parameters"]["InstanceType"]["Default"] == "m7i.2xlarge"   # 8 vCPU / 32 GiB, what the lab was sized for
    assert instances[0]["CreationPolicy"]["ResourceSignal"]["Count"] == 1


def test_nothing_is_exposed_unless_ssh_is_requested(template):
    resources = template["Resources"]
    group = resources["NodeSecurityGroup"]["Properties"]
    assert "SecurityGroupIngress" not in group
    ingress = [r for r in resources.values() if r["Type"] == "AWS::EC2::SecurityGroupIngress"]
    assert [r["Condition"] for r in ingress] == ["HasSsh"]
    assert ingress[0]["Properties"]["FromPort"] == 22 and ingress[0]["Properties"]["ToPort"] == 22


def test_the_node_hardening_defaults(template):
    node = template["Resources"]["Node"]["Properties"]
    assert node["MetadataOptions"]["HttpTokens"] == "required"
    assert node["BlockDeviceMappings"][0]["Ebs"]["Encrypted"] is True
    arns = template["Resources"]["NodeRole"]["Properties"]["ManagedPolicyArns"]
    assert arns == ["arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"]


def test_the_template_deploys_main_and_the_bootstrap_script_is_in_this_tree(template):
    """Users should not have to pick a branch: the node clones main. The script it runs must exist in the repo."""
    assert template["Parameters"]["RepoBranch"]["Default"] == "main"
    assert (AWS / "bootstrap.sh").is_file()


def test_the_dashboard_only_listens_on_localhost():
    text = (AWS / "bootstrap.sh").read_text(encoding="utf-8")
    assert "--server.address 127.0.0.1" in text and "0.0.0.0" not in text
    assert "scripts/images.py pull --stack latest --mode all" in text
    assert "--stack draft06 --mode simulated" in text   # the offline image exists for latest only
