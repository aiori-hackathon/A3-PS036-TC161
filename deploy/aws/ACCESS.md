# Access the CipherOps node through SSM

How a teammate reaches the dashboard (and, if you allow it, a shell) on the AWS node, using their own AWS login. No
SSH key and no open port are needed: the node only talks to AWS Systems Manager.

Two parts: what **the AWS account owner** does once per person, then what **the teammate** does.

---

## Part 1: the account owner, once per person

You need: the **instance ID** and **region** of the node (stack Outputs: `InstanceId`), and the name of an IAM user for
the teammate. Create one for each person, so access can be revoked individually; do not share one login.

**1. Save this as `cipherops-ssm.json`.** Replace `REGION`, `ACCOUNT` and `INSTANCE_ID`.
It allows the dashboard tunnel and a shell on this one instance, and nothing else.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "ssm:StartSession",
      "Resource": [
        "arn:aws:ec2:REGION:ACCOUNT:instance/INSTANCE_ID",
        "arn:aws:ssm:REGION::document/AWS-StartPortForwardingSession",
        "arn:aws:ssm:REGION:ACCOUNT:document/SSM-SessionManagerRunShell"
      ]
    },
    {
      "Effect": "Allow",
      "Action": ["ssm:TerminateSession", "ssm:ResumeSession"],
      "Resource": "arn:aws:ssm:*:*:session/${aws:username}-*"
    }
  ]
}
```

For **dashboard only, no shell**, delete the `SSM-SessionManagerRunShell` line. A shell is full control of the node
(including `sudo`), so give it only to people who need it.

**2. Create the policy and attach it** (the `create-policy` output prints the policy ARN):

```bash
aws iam create-policy --policy-name cipherops-ssm-access --policy-document file://cipherops-ssm.json
aws iam attach-user-policy --user-name TEAMMATE --policy-arn arn:aws:iam::ACCOUNT:policy/cipherops-ssm-access
```

**3. Give the teammate credentials:** in the IAM console, *Users → TEAMMATE → Security credentials → Create access key*,
and turn on MFA for the user. Send the key over a private channel, with the **instance ID** and **region**.

**To revoke:** `aws iam detach-user-policy --user-name TEAMMATE --policy-arn <policy arn>` (and delete their access key).

---

## Part 2: the teammate

You have: an access key, the **instance ID** and the **region**. In the commands below, replace `INSTANCE_ID` with
your instance ID (it looks like `i-0a1b2c3d4e5f67890`; do not run the commands with `INSTANCE_ID` left in), and
`ap-south-1` with your region if it differs.

### Once: install and sign in

1. Install the **AWS CLI v2**: <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html>
2. Install the **Session Manager plugin**: <https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html>
3. Sign in:
   ```bash
   aws configure          # access key id, secret key, default region (ap-south-1), output format (json)
   ```
4. Check both tools work:
   ```bash
   aws sts get-caller-identity       # shows your IAM user
   session-manager-plugin --version  # prints a version
   ```

### Open the dashboard

In a terminal, run this and **leave it running** (it should print `Waiting for connections...`):

```bash
aws ssm start-session --region ap-south-1 --target INSTANCE_ID \
  --document-name AWS-StartPortForwardingSession \
  --parameters portNumber=8501,localPortNumber=8501
```

Then open <http://localhost:8501> in your browser. Close the terminal (Ctrl+C) to disconnect.

If port 8501 is already in use on your computer, use another local port, for example `localPortNumber=8601`, and open
<http://localhost:8601>.

### Get a shell on the node (only if your policy allows it)

In a second terminal:

```bash
aws ssm start-session --region ap-south-1 --target INSTANCE_ID
```

You arrive as `ssm-user`. The project lives in `/opt/cipherops` and belongs to `ec2-user`, so switch before using git:

```bash
sudo -iu ec2-user
cd /opt/cipherops
git pull
```

To switch to another branch, fetch it first. The node's clone is shallow (one commit per branch), and on a node created
before this was fixed it tracks only the branch it was deployed with:

```bash
git remote set-branches origin '*'          # only needed on an older node: allow all branches
git fetch --depth 1 origin BRANCH_NAME
git switch BRANCH_NAME
```

If git refuses because of local changes, `git status` shows them; commit, `git stash`, or discard them first. If the
branch changed the Python requirements, run `.venv/bin/pip install -r requirements-dashboard.txt` and
`sudo systemctl restart cipherops-dashboard`.

Useful checks:

```bash
systemctl status cipherops-dashboard --no-pager      # the dashboard service
curl -s http://127.0.0.1:8501/_stcore/health         # prints: ok
sudo tail -n 80 /var/log/cipherops-bootstrap.log     # the first-boot setup
```

### Rules of the road

- **One person runs things at a time.** There is one lab and one set of results. Two runs at once block each other and
  disturb the timings. Do not run the offline benchmark while the lab is running.
- **Disconnect when you are done** (Ctrl+C on the tunnel, `exit` in the shell). Idle sessions end after about
  20 minutes anyway.
- **Do not store personal tokens on the node**, and do not run git as root. Make code changes on your own machine and
  `git pull` them on the node.
- The dashboard has no login of its own: your AWS access *is* the login. Do not forward the port to others.

### If something goes wrong

| You see | Cause and fix |
| --- | --- |
| `SessionManagerPlugin is not found` | The plugin is not installed or not on your `PATH`. Install it (step 2 above) and check `session-manager-plugin --version`. |
| `AccessDeniedException ... ssm:StartSession` | First check you used your **real instance ID**, not the placeholder (`INSTANCE_ID` or an example such as `i-0123456789abcdef0`). Otherwise your policy does not cover this instance, region or session type (a shell needs the `SSM-SessionManagerRunShell` line), or is not attached to your user. Ask the account owner to run `aws iam list-attached-user-policies --user-name <you>` and compare the instance line in the policy. |
| `TargetNotConnected` | The instance ID or region is wrong, or the node is stopped or still starting. Check both with the account owner. |
| `Unable to locate credentials` | Run `aws configure` again, and check `aws sts get-caller-identity`. |
| The tunnel starts but the page does not load | Check the terminal still says `Waiting for connections...` and that you opened the same local port. If the shell shows `curl ... _stcore/health` is not `ok`, the dashboard service is down: `sudo systemctl restart cipherops-dashboard`. |
| `Address already in use` | Another program uses that local port. Pick a different `localPortNumber`. |
