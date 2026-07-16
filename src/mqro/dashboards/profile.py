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
        site_a_members: Member/instance-name regex selecting site A's instances (e.g.
            ``"nha-rhel-a.*"``). The Native-HA / RDQM per-site instance matrices split on the
            member NAME (a member belongs to a site regardless of which node reported it), not on
            the reporter's ``groups`` label. Empty derives it from ``site_a_group`` (``_``→``-``).
            Was the lab's baked host/instance ``prefix``.
        site_b_members: The site B member/instance-name regex; empty derives from ``site_b_group``.
        drbd_resource: The RDQM DRBD / Pacemaker base resource name (e.g. ``"qmrdqm"``) the RDQM
            board's DRBD and ``cluster_rdqm_pm_state`` queries key on. The DR resource
            (``<base>.dr``) and the Pacemaker clone/IP ids (``p_drbd_<base>`` / ``p_drbd_dr_<base>``
            / ``p_ip_<base>``) derive from it by the rdqmadm/Pacemaker naming convention — one
            input, not five baked literals. RDQM-only; unused by the other arms.
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
    site_a_members: str = ""
    site_b_members: str = ""
    drbd_resource: str = ""

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

    @property
    def nativeha_board_uid(self) -> str:
        """The pinned Native-HA cockpit board uid: ``mqro-<slug>-nativeha``."""
        return f"mqro-{self.slug}-nativeha"

    @property
    def rdqm_board_uid(self) -> str:
        """The pinned RDQM cockpit board uid: ``mqro-<slug>-rdqm``."""
        return f"mqro-{self.slug}-rdqm"

    @property
    def site_a_member_selector(self) -> str:
        """Site A's member/instance-name regex — the explicit ``site_a_members`` when set, else
        derived from ``site_a_group`` by the lab convention (``_``→``-`` + ``.*``)."""
        return self.site_a_members or f"{self.site_a_group.replace('_', '-')}.*"

    @property
    def site_b_member_selector(self) -> str:
        """Site B's member/instance-name regex — the explicit ``site_b_members`` when set, else
        derived from ``site_b_group`` by the lab convention (``_``→``-`` + ``.*``)."""
        return self.site_b_members or f"{self.site_b_group.replace('_', '-')}.*"

    @property
    def all_members_selector(self) -> str:
        """The both-sites member/node regex — the alternation of the two site selectors. Scopes
        the RDQM storage matrix (which spans every node running the HA DRBD resource)."""
        return f"{self.site_a_member_selector}|{self.site_b_member_selector}"

    @property
    def drbd_dr_resource(self) -> str:
        """The RDQM cross-site DR DRBD resource — ``<drbd_resource>.dr`` (rdqmdr convention)."""
        return f"{self.drbd_resource}.dr"

    @property
    def pm_drbd_ha_resource(self) -> str:
        """The Pacemaker clone id for the HA DRBD resource — ``p_drbd_<drbd_resource>``."""
        return f"p_drbd_{self.drbd_resource}"

    @property
    def pm_drbd_dr_resource(self) -> str:
        """The Pacemaker clone id for the DR DRBD resource — ``p_drbd_dr_<drbd_resource>``."""
        return f"p_drbd_dr_{self.drbd_resource}"

    @property
    def pm_ip_resource(self) -> str:
        """The Pacemaker floating-IP resource id — ``p_ip_<drbd_resource>``."""
        return f"p_ip_{self.drbd_resource}"
