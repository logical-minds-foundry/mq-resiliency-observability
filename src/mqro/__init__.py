"""mqro — MQ Resiliency Observability.

Stdlib-only collectors that project CLI-only IBM MQ HA/DR state (Native HA, and later
RDQM and Pacemaker/DRBD) into node_exporter textfile metrics, supplementing the stock
mq_prometheus exporter. This package is the foundation extracted from the lab; it owns
the collectors only — packaging, detection, and dashboards live in sibling tasks.
"""
