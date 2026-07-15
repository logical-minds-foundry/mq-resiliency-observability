# mq-resiliency-observability

Non-MQI IBM MQ HA/DR observability: stdlib Python collectors and Grafana
dashboards that map CLI-only MQ and cluster state (Native HA, RDQM,
Pacemaker/DRBD) into Prometheus metrics, supplementing the stock mq_prometheus
exporter. Packaged as signed .rpm and .deb.

## Table of Contents

- [Status](#status)
- [Integration — read this first](#integration--read-this-first)
- [The metrics contract](#the-metrics-contract)
- [Getting Started](#getting-started)
- [License](#license)

## Status

Early development.

## Integration — read this first

Two wiring prerequisites decide whether you see any data at all. Both fail
**silently**: the collectors run happily, no error is raised, and the dashboards
render **empty**. Get these right before anything else.

### Trap 1 — the required scrape-side labels (dashboards render empty without them)

The dashboards' PromQL assumes labels the collectors **do not emit**: a `groups`
label (sourced from the Ansible inventory groups a host belongs to) plus
host-name patterns. These are relabeling the **operator** configures on the
Prometheus side — a stock Prometheus has none of them, so the boards match
nothing and render empty. This is the single biggest trap for an adopter.

Add relabeling that stamps the `groups` label onto every scraped target. The
mechanics depend on your service discovery; the shape is:

```yaml
scrape_configs:
  - job_name: mq-nodes
    static_configs:
      - targets: ["nha-rhel-a1:9100"]
        labels:
          groups: nha-live          # the Ansible inventory group(s) for this host
    relabel_configs:
      # Keep the host's short name in a stable label the boards key on.
      - source_labels: [__address__]
        regex: "([^.:]+).*"
        target_label: node
        replacement: "$1"
```

The collectors already stamp a `node` label themselves; the snippet above shows
where the **scrape-side** additions (notably `groups`) live. Match the label
values to whatever your dashboards' queries expect.

### Trap 2 — the node_exporter textfile directory (a boundary we declare, never mutate)

The collectors write `.prom` files into a directory that node_exporter's
**textfile collector** must be configured to read. If node_exporter is not
watching that directory, the files pile up unread and no metric ever appears.

This directory is **your** configuration input, not an assumed default — you tell
the collectors where to write via the runtime profile's `textfile_dir`, and it
must be the same path node_exporter's `--collector.textfile.directory` points at.

We **declare-and-verify; we never mutate** node_exporter's config — it is across
the ownership boundary and may be a package-owned or config-managed file that is
not ours to touch. Concretely:

- Point node_exporter at the directory yourself (its flag / unit config). We
  document the requirement; wiring it is the integrator's responsibility.
- The collector's service user must be able to write the directory. The lab uses
  a `02775` setgid directory managed by `systemd-tmpfiles`; document the
  permissions for your fleet rather than assuming we can `chmod` a shared path we
  do not own.
- Each run self-checks the directory and, on failure, emits a loud health signal
  and a stale `cluster_state_last_write_timestamp` — never a silent gap.

## The metrics contract

The contract has two halves, both versioned in the component:

1. **Emitted metrics** — the `cluster_*` / `cluster_nha_*` families the collectors
   emit, declared as structured data in
   [`src/mqro/contract.py`](src/mqro/contract.py) (the authoritative source). A
   consistency test ([`tests/test_contract.py`](tests/test_contract.py)) asserts
   every metric the contract names is one a collector actually emits, and every
   metric a collector emits is one the contract names — checked in both
   directions, at name and label-key granularity.
2. **Required scrape-side labels** — the labels the dashboards assume but the
   collectors do not emit (Trap 1 above). These are operator configuration, not
   emitted series, so they live in this README rather than in `contract.py`.

The Native HA families (slice 1), all carrying a `node` label:

| Metric | Extra labels | Value |
| --- | --- | --- |
| `cluster_quorate` | — | 1 when quorum met, else 0 (omitted if unknown) |
| `cluster_nha_quorum` | — | current quorum vote count |
| `cluster_node_online` | `member` | 1 when the instance's role is known |
| `cluster_nha_role` | `member`, `role` | info series (value 1); role in the label |
| `cluster_nha_role_code` | `member` | 2=Active, 3=Leader, 1=Replica, 0=other |
| `cluster_nha_insync` | `member` | 1 when in sync with the leader |
| `cluster_nha_hastatus` | `member`, `status` | info series (value 1); HASTATUS label |
| `cluster_nha_hastatus_ok` | `member` | 1 when HASTATUS is Normal |
| `cluster_resource_owner` | `resource`, `holder` | 1 for the member running the QM |
| `cluster_nha_group_role` | `group`, `role` | info series (value 1); GRPROLE label |
| `cluster_nha_group_status` | `group`, `status` | info series (value 1); GRSTATUS label |
| `cluster_nha_connected` | `group` | 1 when a recovery group is connected (CRR) |
| `cluster_nha_group_insync` | `group` | 1 when a recovery group is in sync |
| `cluster_nha_group_backlog` | `group` | CRR backlog, message count |
| `cluster_state_last_write_timestamp` | `source` | Unix time of last fresh write per source |

The RDQM and Pacemaker/DRBD families, and the dashboard-side half of the
consistency check, arrive with later slices.

## Getting Started

See the [documentation](https://logical-minds-foundry.github.io/mq-resiliency-observability/).

## License

MIT — see [LICENSE](LICENSE).
