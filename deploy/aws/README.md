# Run CipherOps on AWS

[`cipherops-node.yaml`](cipherops-node.yaml) creates one EC2 instance (default **m7i.2xlarge**: 8 vCPU, 32 GiB) that
runs both the offline benchmark and the simulated lab, with its own VPC. It pulls the pinned images from a registry
(no compiling for tens of minutes) and serves the dashboard on the instance's `localhost:8501`. No inbound port is open:
you reach it through SSM.

## Steps

**1. Once: tools and access**

- An AWS account and a user or role that can create CloudFormation stacks, EC2 and VPC resources, and an IAM role.
- The [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), signed in
  (`aws configure`, or `aws configure sso` then `aws sso login`). Check with `aws sts get-caller-identity`.
- The [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
  (`session-manager-plugin --version` should answer). This is what opens the tunnel.
- Quota: the instance needs 8 vCPUs of *Running On-Demand Standard instances* in your region. New accounts sometimes
  start lower; check Service Quotas if the stack fails with `VcpuLimitExceeded`.

**2. Deploy** (Mumbai shown; use any region)

```bash
git clone https://github.com/aiori-hackathon/A3-PS036-TC161.git && cd A3-PS036-TC161
aws cloudformation deploy --region ap-south-1 --stack-name cipherops \
  --template-file deploy/aws/cipherops-node.yaml --capabilities CAPABILITY_IAM
```

The node clones `main` by default, so there is no branch to pick.

The command waits until the node reports it is ready: typically 10 to 20 minutes, mostly the image pulls. To watch it,
open CloudFormation → *cipherops* → Events, or run
`aws cloudformation describe-stack-events --region ap-south-1 --stack-name cipherops --max-items 5`.

**3. Open the dashboard**

```bash
aws cloudformation describe-stacks --region ap-south-1 --stack-name cipherops \
  --query "Stacks[0].Outputs[?OutputKey=='DashboardTunnel'].OutputValue" --output text
```

Run the command it prints and leave it running, then open <http://localhost:8501>. Both **Node ready** panels should
be green already, so go straight to **Offline benchmark → ① Run**, or **Simulated lab → ① Prepare**. To let a teammate
in with their own AWS login, give them [ACCESS.md](ACCESS.md).

**4. Get your results out, then delete the stack**

Results live on the instance (`/opt/cipherops/results` for offline runs, `/opt/cipherops/ecosystem/results` for the
lab). Download what you need with the dashboard's download buttons. For everything else, deploy with `KeyName` and
`SshCidr` set and use `scp` (the instance role has no S3 access, and Session Manager does not copy files). Then:

```bash
aws cloudformation delete-stack --region ap-south-1 --stack-name cipherops
```

An instance bills by the hour while it exists. Stopping it (EC2 console → *Stop instance*) pauses the compute charge
and keeps the disk and images; start it again later and the dashboard service comes back by itself.

## If the stack fails

A failed bootstrap rolls the stack back and takes the instance's log with it. Create it without rollback to keep the
instance, then read the log:

```bash
aws cloudformation create-stack --region ap-south-1 --stack-name cipherops --on-failure DO_NOTHING \
  --template-body file://deploy/aws/cipherops-node.yaml --capabilities CAPABILITY_IAM
aws ssm start-session --region ap-south-1 --target <instance id>     # then: sudo tail -n 80 /var/log/cipherops-bootstrap.log
```

Common causes: the registry images are private (pull is denied), the branch has no `deploy/aws/bootstrap.sh`, or the
account cannot reach the internet from the new VPC.

## Image registries

The pinned images are published in two public registries, and **either one works** for the node. It pulls the offline
image (`latest`) and the lab images for `draft06`; both registries have all four.

| Registry | Offline `latest` | Lab `draft06` | Lab `latest` | Used by |
| --- | --- | --- | --- | --- |
| `ghcr.io/anithapriyanatarajan/benchmark` | yes | yes | yes | **this template's default** |
| `ghcr.io/hackchamp25` | yes | yes | no (not published) | the dashboard's default pull registry on `ui-two-tracks` |

- To use the team registry: `--parameter-overrides Registry=ghcr.io/hackchamp25`.
- Lab images for `latest` are not pulled at boot, since the lab is used with `draft06`. If you pick `latest` in the
  Simulated lab's prerequisite panel, it shows them as missing and can pull them, but only a registry that has them
  (today `ghcr.io/anithapriyanatarajan/benchmark`) will succeed.
- The node's dashboard uses the `Registry` you deploy with as its default pull registry, whatever the repository's own
  default is, so a pull from the dashboard's prerequisite panel goes to the same place as the bootstrap.
- A pull from either registry is verified against the stack profile, so an image built from other pins is refused.

## Parameters

Set with `--parameter-overrides Name=value` on `deploy`.

| Parameter | Default | Notes |
| --- | --- | --- |
| `InstanceType` | `m7i.2xlarge` | x86_64 only (the images are amd64). 8 vCPU / 32 GiB, what the simulated lab was sized for. `c7i.2xlarge` (16 GiB) is cheaper. |
| `VolumeSizeGiB` | `100` | gp3, encrypted. Minimum 64. |
| `RepoUrl`, `RepoBranch` | this repository, `main` | What is checked out to `/opt/cipherops`. The branch must contain `deploy/aws/bootstrap.sh`. |
| `Registry` | `ghcr.io/anithapriyanatarajan/benchmark` | Must be public. `ghcr.io/hackchamp25` works too: see [Image registries](#image-registries). |
| `KeyName`, `SshCidr` | empty | Set both to also allow SSH from that range. The dashboard is never exposed. |

## Good to know

- **Timings.** An EC2 vCPU is a hyperthread on shared hardware. Offline numbers are indicative, and the simulated lab
  shares all CPUs by design. For the cleanest offline timings use a bare-metal type (for example `m7i.metal-24xl`),
  and do not run the lab at the same time as an offline run.
- **Security.** The dashboard has no login and can run Docker, so it only listens on `127.0.0.1`; IMDSv2 is required
  and the root volume is encrypted.
