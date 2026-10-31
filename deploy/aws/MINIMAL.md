# Minimal mode: a read-only dashboard for evaluators

Minimal mode is a second copy of the dashboard that lets visitors explore a running CipherOps node without being able to
change it. The team's own dashboard and the lab keep running as they are. The team's instance is at
**https://eval.cipherops.stream**.

## What visitors can and cannot do

**Visitors can**
- read every finished offline run, its report and downloads (**Offline benchmark → Results**);
- send lookups and read the answer, size, AD flag and latency (**Simulated lab → Query**), and run `dig` in a restricted
  terminal (below);
- **switch the active algorithm** between the ones the lab has signed (below);
- browse the team's saved queries, packet captures, benchmarks and reports, and use **Compare** and **Learn**.

**Visitors cannot**
- build or pull images, sign zones, start or stop the lab, flush the resolver cache or capture packets;
- start or cancel an offline or lab benchmark;
- Reset, or open the Node images page.

Every page shows a notice that this is minimal mode, with a pointer to the README for a full copy.

**Switching algorithms** reloads the signed zones of the chosen algorithm and restarts the lab's servers, which affects
every visitor at once. It is therefore limited to algorithms the lab has signed, keeps the lab's current UDP size, takes
effect only when the visitor presses **Switch to …** (never because a selector is out of date), and is one switch at a
time for the whole node with a 15-second pause after each. A switch waits for lookups already running, and lookups sent
during it are told the lab is busy. Results taken on one algorithm are labelled with it, and the Query page warns when the
lab has moved on.

**The terminal** runs `dig` only, with no shell. It accepts `+options` and the dash options `-4 -6 -x -t -q -c -p`, can
reach only the lab's own servers (10.53.0.0/24), times out after 10 seconds, and allows one command per visitor every
3 seconds and three at a time across all visitors.

**Nothing relies on hiding a button.** Every function that changes the lab refuses in minimal mode, whatever calls it
([`dashboard/live_lab/minimal.py`](../../dashboard/live_lab/minimal.py), tested in
[`tests/test_minimal.py`](../../tests/test_minimal.py)). Lookup inputs are validated and quoted, and a visitor's lookups
are not written to the team's files: the lab config, saved preferences, query session, activity log and job notices
stay as they were.

## Run it locally

The same app as the full dashboard, with `CIPHEROPS_MINIMAL=1` in the **process environment**. Use another port so the full
dashboard can stay on 8501. It worked when every page shows the **Minimal mode** notice and the lab controls (build, sign,
start/stop, Run benchmark, Reset) are refused. Live lookups need a lab already running (Simulated lab → Prepare → Start in
the full dashboard).

```bash
# Linux / macOS / Git Bash
CIPHEROPS_MINIMAL=1 streamlit run dashboard/live_lab/app.py --server.address 127.0.0.1 --server.port 8502
```

```powershell
# Windows PowerShell: use $env:CIPHEROPS_MINIMAL. `$CIPHEROPS_MINIMAL=1` only sets a PowerShell variable that Streamlit
# never sees, so port 8502 would look identical to the full dashboard. The value must be exactly 1.
$env:CIPHEROPS_MINIMAL = "1"
python -m streamlit run dashboard/live_lab/app.py --server.address 127.0.0.1 --server.port 8502
```

For an optional access code (asked once per browser session), also set `CIPHEROPS_MINIMAL_PASSWORD='choose-a-code'`
(PowerShell: `$env:CIPHEROPS_MINIMAL_PASSWORD = "choose-a-code"`). To return to a full session in the same PowerShell
window: `Remove-Item Env:CIPHEROPS_MINIMAL, Env:CIPHEROPS_MINIMAL_PASSWORD -ErrorAction SilentlyContinue`.

## Run it on the AWS node

On the AWS node, open a shell (Session Manager, see [ACCESS.md](ACCESS.md)) and use `minimal.sh`. It starts the minimal
dashboard on `127.0.0.1:8502` and puts it on the internet through an outbound Cloudflare tunnel, so no inbound port or
security-group rule is needed and the team's dashboard on 8501 is never exposed.

| Command | Does |
| --- | --- |
| `sudo bash deploy/aws/minimal.sh start` | a temporary `*.trycloudflare.com` link; it changes whenever the tunnel restarts |
| `sudo -E TUNNEL_TOKEN='<token>' bash deploy/aws/minimal.sh start --domain eval.example.com` | a fixed address you own (next section) |
| `sudo bash deploy/aws/minimal.sh url` / `status` | print the link / show both services and the link |
| `sudo bash deploy/aws/minimal.sh stop` | take the link and the minimal dashboard down |

Add an access code as the last argument (`start 'code'`) to make visitors type it first; without one, anyone with the
link can open it. To remove a code that was set earlier: `sudo rm /etc/cipherops/minimal.env && sudo systemctl restart cipherops-minimal`.

## A fixed address (named tunnel)

Needs a domain whose DNS is on Cloudflare (free account; the Zero Trust free plan may ask for a payment card but does not
charge). The address then survives restarts and reboots.

1. In Cloudflare, create a tunnel: **Zero Trust (Cloudflare One) → Networks → Connectors → Cloudflare Tunnels → Create a
   tunnel → Cloudflared**. Copy the token (the long `eyJ…` string in the install command). Do not run that command; the
   script installs the service itself.
2. On that tunnel, add a **published application route** (older name: *Public Hostname*): subdomain `eval`, your domain,
   service **HTTP**, URL **`localhost:8502`**. This creates the DNS record. Without the route the tunnel is healthy but
   the name does not resolve.
3. On the node run the `--domain` command above. It stores the token in `/etc/cipherops/minimal-tunnel.env` (root only),
   waits for the tunnel to connect and for the address to answer, and prints it. `url` prints the same address from then
   on. Treat the token like a password; rotate it in Cloudflare if it leaks.

## Staying up

Both services start at boot and restart if they fail. `start` also asks Docker to bring the lab's containers back after a
reboot (restart policy `unless-stopped`, and `CIPHEROPS_LAB_RESTART` for labs the team's dashboard starts later), so
after a restart the link, the dashboard and the lab return by themselves; `stop` turns the policy off. The team's
dashboard picks up the setting at its next restart. `start` does not restart it, because restarting a service ends any
background job it launched.

Visitors can only query a lab that is signed and running, and switch between the algorithms it was signed with. A benchmark suite
stops its own lab when it ends, so start the lab again from the team's dashboard afterwards. Saved results are available
either way.

## Troubleshooting

| You see | Cause and fix |
| --- | --- |
| `Could not resolve host` | No DNS record: add the published application route (step 2 above). |
| 502 Bad Gateway | The minimal dashboard is not running: `sudo bash deploy/aws/minimal.sh status`, and check the route's URL is `localhost:8502`. |
| An access-code box you did not want | A code is stored: remove `/etc/cipherops/minimal.env` and restart `cipherops-minimal`. |
| Query says it needs a ready lab | Sign zones and start the lab from the team's dashboard. |
| `start --domain` says the tunnel did not connect | Wrong or truncated token, or the node cannot reach Cloudflare outbound (443 / 7844). |
| `systemctl restart` seems to hang | It is waiting for the service to stop; Ctrl+C, then `systemctl status cipherops-minimal`. |

## Limits

- The minimal dashboard runs as a user in the `docker` group (lookups go through `docker compose exec`), which is
  root-equivalent on the node. The refusals, input validation and shell-free terminal above are what contain it; stop the
  link when an evaluation ends, and prefer an access code for anything long-lived.
- Quick tunnels have no uptime guarantee. A named tunnel does not have that limit.
- The node bills while it runs; when it is stopped the link is down.
- Visitors share one resolver, so its cache reflects whatever someone else just asked.
