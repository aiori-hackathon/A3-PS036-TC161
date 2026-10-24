# Cipherops · MTL DNSSEC Lab

```powershell
cd C:\MTL\Cipherops_new\A3-PS036-TC161
streamlit run dashboard/live_lab/app.py
```

## Sidebar · Lab options

| Control | Effect |
|---------|--------|
| **Zone size** | Small / Medium / Large → writes `configs/_dashboard.yaml` |
| **Scenarios** | Which algorithms to sign and benchmark |
| **Sign jobs** | Parallelism for Prepare |
| **Queries / trial** | Workload size for Step 6 |
| **Trials** | Cold+warm repetitions for Step 6 |
| **Network profiles** | lan / wan / lossy |
| **UDP buffer** | 1232 or 4096 |

## Requirements

- Docker (eco images: build in UI or pull from a registry)
- `pip install -r requirements-dashboard.txt`

## Steps

1. **Images** — Build locally or Pull from registry  
2. **Prepare** — sign zones; Lab tab shows work/zones/active/compose paths  
3. **Start** — bring lab up  
4. **Activate** — change scenario (auto when running)  
5. **Query** — Lab tab live probe + capture  
6. **Run benchmark** — `ecolab run`

**Packets:** Capture decode (story + table) · Sequence diagram (pcap → Decode → local PNG)

## Reset

Sidebar: clear results / pcaps / optional work wipe.
