"""The dashboard profile — every identity the lab's boards baked in, extracted as one input.

A :class:`DashboardProfile` is the single source of truth a board render binds to. Its fields are
exactly the de-hardcoding checklist: QM identity (derived from the #351 ``short`` token the way
``mqlab.stacks`` derives it — never a competing source), the site Ansible groups, the
Pacemaker/DRBD resource name, the log host patterns, and the datasource UIDs. Nothing about a
board's PromQL is a constant any more; it all threads through here.

QM-name semantics mirror ``mqlab.stacks`` verbatim: the app queue manager is ``<short>APP`` and the
counterparty is the shared ``<svc_short>QM`` (#446), and this stack's own request queue on that
shared SVCQM is ``<short>.SVC.REQUEST``. Deriving them here (not copying literals) is what keeps the
generator and the lab's stack registry from drifting.

Stdlib-only (a frozen dataclass with computed properties); no branches, so it is trivially
deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DashboardProfile:
    """What a board render needs to know, all of it parameterized (nothing hardcoded).

    Attributes:
        slug: Board identity used in UIDs and tags (e.g. ``"pcmk-ubuntu"``). The lab's boards
            derived their uid/filename from the stack name; ``slug`` is that, generalized.
        short: The stack's #351 token. The app QM name derives from it (``<short>APP``), as does
            this stack's request queue (``<short>.SVC.REQUEST``) — never a QM literal.
        svc_short: The shared counterparty's token (#446). The SVC QM name derives from it
            (``<svc_short>QM``).
        site_a_group: Ansible group for site A (e.g. ``"pcmk_a"``). Was baked into ``groups=~``
            selectors so every board query stays scoped to its own cluster's nodes.
        site_b_group: Ansible group for site B (e.g. ``"pcmk_b"``).
        qm_resource: The resource id ``cluster_resource_owner`` / ``cluster_resource_started``
            key on — a Pacemaker resource id (``"mq_qm"``) for the SAN arm, or the QM name for the
            Native-HA / RDQM arms. Was a bare constant per board.
        log_host_patterns: Host-name regexes the log panel scopes to (e.g. ``("pcmk-.*",
            "san-.*")``). Empty renders a log panel with no host scope.
        title: Human board title (the spelled-out banner). Defaults to a slug-derived title.
        datasource_uid: The Prometheus datasource UID (was the hardcoded ``"prometheus"``).
        logs_uid: The Loki datasource UID (was the hardcoded ``"loki"``).
    """

    slug: str
    short: str
    svc_short: str
    site_a_group: str
    site_b_group: str
    qm_resource: str
    log_host_patterns: tuple[str, ...] = ()
    title: str = ""
    datasource_uid: str = "prometheus"
    logs_uid: str = "loki"

    @property
    def app_qm(self) -> str:
        """The app/HA queue manager name — ``<short>APP`` (``mqlab.stacks`` semantics)."""
        return f"{self.short}APP"

    @property
    def svc_qm(self) -> str:
        """The shared counterparty queue manager name — ``<svc_short>QM`` (#446)."""
        return f"{self.svc_short}QM"

    @property
    def req_queue(self) -> str:
        """This stack's own request queue on the shared SVCQM — ``<short>.SVC.REQUEST`` (#446)."""
        return f"{self.short}.SVC.REQUEST"

    @property
    def groups_selector(self) -> str:
        """The ``groups=~`` alternation scoping a query to this cluster's two sites."""
        return f"{self.site_a_group}|{self.site_b_group}"

    @property
    def host_selector(self) -> str:
        """The ``host=~`` alternation for the log panel, or ``.*`` when no patterns are given."""
        return "|".join(self.log_host_patterns) if self.log_host_patterns else ".*"

    @property
    def board_title(self) -> str:
        """The board title — the explicit ``title`` when set, else a slug-derived fallback."""
        return self.title or f"{self.slug} — cluster"

    @property
    def qm_board_uid(self) -> str:
        """The pinned per-QM board uid: ``mqro-qm-<short>`` (lowercased)."""
        return f"mqro-qm-{self.short.lower()}"

    @property
    def messaging_board_uid(self) -> str:
        """The pinned messaging board uid: ``mqro-messaging-<slug>``."""
        return f"mqro-messaging-{self.slug}"

    @property
    def cluster_board_uid(self) -> str:
        """The pinned cluster-cockpit board uid: ``mqro-<slug>-cluster``."""
        return f"mqro-{self.slug}-cluster"
