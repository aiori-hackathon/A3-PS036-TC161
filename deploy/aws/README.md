# Run CipherOps on AWS

[`cipherops-node.yaml`](cipherops-node.yaml) creates one EC2 instance that runs **both** the offline benchmark and the
simulated lab, with its own VPC. It pulls the pinned images from a registry (no compiling for tens of minutes) and
serves the dashboard on the instance's `localhost:8501`. No inbound port is open: you reach it through SSM.

## Deploy

You need the AWS CLI v2 with credentials, and the [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html).

```bash
aws cloudformation deploy --stack-name cipherops --template-file deploy/aws/cipherops-node.yaml \
  --capabilities CAPABILITY_IAM
```

The stack finishes when the dashboard answers on the instance (typically 10 to 20 minutes, mostly the image pulls). To
keep a failed instance for inspection instead of rolling back, create it with
`aws cloudformation create-stack ... --on-failure DO_NOTHING --capabilities CAPABILITY_IAM`.

## Open the dashboard

```bash
aws cloudformation describe-stacks --stack-name cipherops --query "Stacks[0].Outputs[?OutputKey=='DashboardTunnel'].OutputValue" --output text
```

Run the command it prints, then open <http://localhost:8501>. The **Node ready** panels should already be green.
`Shell` (same outputs) gives a shell on the node; the bootstrap log is `/var/log/cipherops-bootstrap.log`.

## Parameters

| Parameter | Default | Notes |
| --- | --- | --- |
| `InstanceType` | `c7i.2xlarge` | x86_64 only (the images are amd64). 8 vCPU / 16 GiB. The simulated lab pins containers to cores 2-5 and was sized at 8 vCPU / 32 GiB: use `m7i.2xlarge` if you see memory pressure. |
| `VolumeSizeGiB` | `100` | gp3, encrypted. |
| `RepoUrl`, `RepoBranch` | this repository, `ui-two-tracks` | What is checked out to `/opt/cipherops`. |
| `Registry` | `ghcr.io/anithapriyanatarajan/benchmark` | Must be public. |
| `KeyName`, `SshCidr` | empty | Set both to also allow SSH from that range. The dashboard is never exposed. |

## Good to know

- **Timings.** An EC2 vCPU is a hyperthread on shared hardware. Offline numbers are indicative, and the simulated lab
  shares all CPUs by design. For the cleanest offline timings use a bare-metal type (for example `c7i.metal-24xl`),
  and do not run the lab at the same time as an offline run.
- **Security.** The dashboard has no login and can run Docker, so it only listens on `127.0.0.1`; IMDSv2 is required
  and the root volume is encrypted.
- **Cost.** An instance bills by the hour while it exists. Delete it when you are done:
  `aws cloudformation delete-stack --stack-name cipherops`. Results live on the instance, so download them first
  (Reset and the results tables work as usual; `/opt/cipherops/results` holds the offline runs).
