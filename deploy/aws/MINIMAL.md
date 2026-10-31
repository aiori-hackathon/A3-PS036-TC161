# Minimal mode: let evaluators explore a running node

Minimal mode is a second copy of the dashboard for evaluators. It shows what the team has already run, lets visitors
send DNS lookups to the live lab, and refuses everything that would change the lab. The team's own dashboard and the
lab keep running as they are.

## What evaluators can and cannot do

| Can | Cannot |
| --- | --- |
| **Offline benchmark → Results**: every finished run, its report and downloads | build or pull images, run or cancel an offline benchmark |
| **Simulated lab → Query**: send a lookup and read the answer, size, AD flag and latency | switch the active algorithm (it restarts the shared servers) |
| **Simulated lab → Query → dig terminal**: a restricted terminal (below) | flush the resolver cache, or capture packets |
| **Simulated lab → Query → Results**: the team's saved queries and packet captures | sign zones, start or stop the lab |
| **Simulated lab → Benchmark**: saved benchmarks, reports and downloads | start or cancel a benchmark |
| **Compare** and **Learn** | Reset, or open the Node images page |

Every page shows the notice that this is minimal mode, and how to deploy a full copy (the README: locally, in
Codespaces, or on AWS).

**The restricted terminal** runs `dig` only, with no shell. It accepts `+options` and only the dash options `-4 -6 -x
-t -q -c -p`, can reach only the lab's own servers (10.53.0.0/24), times out after 10 seconds, and allows one command
per visitor every 3 seconds and three at a time across all visitors.

**Nothing is enforced only by hiding a button.** The functions that change the lab refuse in minimal mode, whatever
calls them (`dashboard/live_lab/minimal.py`, tested in `tests/test_minimal.py`). A visitor's lookups are also not
written to the team's files: the lab config, saved preferences, query session, activity log and job notices stay as
the team left them.

## Run it on the node

Open a shell on the node (Session Manager, see [ACCESS.md](ACCESS.md)), then:

```bash
cd /opt/cipherops
sudo bash deploy/aws/minimal.sh start 'choose-an-access-code'   # omit the code to let anyone with the link in
```

It starts the minimal dashboard on `127.0.0.1:8502` and prints a public HTTPS link such as
`https://<random-words>.trycloudflare.com`. Send the evaluators the link and the access code.

| Command | Does |
| --- | --- |
| `sudo bash deploy/aws/minimal.sh url` | print the current link |
| `sudo bash deploy/aws/minimal.sh status` | both services and the link |
| `sudo bash deploy/aws/minimal.sh stop` | take the link and the minimal dashboard down |

Before sharing the link, make sure the lab is signed and running with the algorithm you want evaluators to see
(Simulated lab → Prepare in the team's dashboard). Evaluators cannot change it.

**How the link works.** It is a Cloudflare quick tunnel: the node makes an outbound connection, so no inbound port,
security-group rule or domain is needed, and the team's dashboard on port 8501 is never exposed. The address is
temporary and changes if the tunnel restarts (for example after the node is stopped and started); run `url` again and
resend it. For a fixed address, use a named Cloudflare tunnel on your own domain instead.

## Message for the evaluators

> You can explore our running CipherOps node at **<link>** (access code **<code>**). It opens in **minimal mode**:
> you can browse the offline and simulated-lab results we have run, compare algorithms, follow the Learn
> walkthroughs, and send DNS lookups to the live lab from the Query step or its dig terminal. Changing the lab is
> turned off so the shared environment stays as it is. To experience every feature, deploy your own copy by
> following the README (https://github.com/aiori-hackathon/A3-PS036-TC161#readme): on your machine, in GitHub
> Codespaces, or on AWS with `deploy/aws`.

## Limits worth knowing

- The minimal dashboard runs as a user in the `docker` group (it sends lookups through `docker compose exec`), which
  is root-equivalent on the node. That is why every lab-changing function refuses in code, lookup inputs are
  validated and quoted, and the terminal runs `dig` without a shell. Keep an access code on the link and stop it
  (`minimal.sh stop`) when evaluation is over.
- Quick tunnels are meant for short-term sharing, with no uptime guarantee. If the link stops working, run `start`
  again.
- Lookups from several evaluators share one resolver, so its cache is warm for whatever someone else just asked.
