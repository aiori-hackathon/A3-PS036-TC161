# Run in GitHub Codespaces

The repository ships a dev container ([`.devcontainer/devcontainer.json`](../.devcontainer/devcontainer.json)), so you
can run CipherOps in the browser with nothing installed locally.

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/aiori-hackathon/A3-PS036-TC161)

The dev container installs Python 3.12 and the dashboard requirements, runs Docker inside the codespace (so the compose
bind mounts work as they do locally), forwards port 8501 and sets the Streamlit options the Codespaces proxy needs.

## Which machine for what

Observed while verifying in Codespaces:

| Machine (cores / RAM / storage) | Free tier | Offline harness | Simulated lab (Verisign stack) |
| --- | --- | --- | --- |
| 4 / 16 GB / 32 GB | yes | works | **does not work**: the lab runs about 7 containers pinned to cores 2-5 |
| 8 / 32 GB / 64 GB | no (paid) | works | works |

The dev container requests the 4 / 16 / 32 minimum so free users can open it and run the offline benchmark. For the
simulated lab, pick the 8 / 32 / 64 machine when creating the codespace (or change the machine type of an existing one
under **Codespaces → Change machine type**), or run it locally (see [Getting started](getting-started.md)).

## Steps

1. Click the badge above and choose a **4-core** machine for the offline benchmark, or an **8-core** one for the
   simulated lab (see the table above).
2. Wait for the container to finish creating, then start the dashboard and open port 8501 from the **Ports** tab:

   ```bash
   python -m streamlit run dashboard/live_lab/app.py --server.port 8501
   ```

3. Open **Offline benchmark**. If its prerequisite panel says the image is missing, press **Build image** (a 4-core
   machine can build it) or **Pull from registry**, then **① Run → Quick check**. **Simulated lab** has the same panel
   for its three lab images.

The command line works too: see [Getting started](getting-started.md#offline-benchmark-command-line).

## Things to know

- **Timings are indicative.** A codespace's virtual CPUs are shared with other tenants. Use Codespaces to try the tool and
  read byte-level results; for timings you report, use dedicated hardware ([AWS](../deploy/aws/README.md)).
- **Builds are slow and a codespace stops when idle.** Keep the tab open or raise the idle timeout in your Codespaces
  settings; builds and runs are background jobs and continue while the codespace is up.
- **Results live in the codespace.** Download `results/runs/<id>/` before you delete it.
- **`NO_PUBKEY 62D54FD4003F6525` while building the container** is the Yarn apt key, rotated upstream. That is why the
  configuration uses the plain `base` image and adds Python as a feature. Do not disable apt's signature checks; change
  the base image or remove the Yarn source, and report the log.
- **Not confirmed in a real codespace:** live validation with network emulation (`scripts/run_live.sh`) needs
  `NET_ADMIN`, which the docker-in-docker daemon should allow.
