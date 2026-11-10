"""Схема SQLite. Секреты лежат только в столбцах *_enc."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    host: Mapped[str] = mapped_column(String(255))
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_user: Mapped[str] = mapped_column(String(64), default="root")
    auth_type: Mapped[str] = mapped_column(String(16), default="password")
    ssh_secret_enc: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(32))
    country: Mapped[str] = mapped_column(String(8), default="")
    status: Mapped[str] = mapped_column(String(16), default="unknown")
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    awg_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reality_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hysteria_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sub_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    link_protocol: Mapped[str] = mapped_column(String(16), default="vless")
    protocols_json: Mapped[str] = mapped_column(Text, default="")
    awg_public: Mapped[str] = mapped_column(Text, default="")
    awg_private_enc: Mapped[str] = mapped_column(Text, default="")
    awg_params_json: Mapped[str] = mapped_column(Text, default="")
    hpk_enc: Mapped[str] = mapped_column(Text, default="")
    reality_public: Mapped[str] = mapped_column(Text, default="")
    reality_private_enc: Mapped[str] = mapped_column(Text, default="")
    reality_short_enc: Mapped[str] = mapped_column(Text, default="")
    reality_sni: Mapped[str] = mapped_column(String(255), default="www.googletagmanager.com")
    node_uuid: Mapped[str] = mapped_column(String(36), default="")
    precheck_json: Mapped[str] = mapped_column(Text, default="")
    stats_json: Mapped[str] = mapped_column(Text, default="{}")
    agent_status_json: Mapped[str] = mapped_column(Text, default="")
    config_dirty: Mapped[bool] = mapped_column(Boolean, default=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    clients: Mapped[list["Client"]] = relationship(back_populates="server")


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16), default="pc")
    protocol: Mapped[str] = mapped_column(String(16), default="awg")
    server_id: Mapped[str | None] = mapped_column(ForeignKey("servers.id"), nullable=True)
    address: Mapped[str] = mapped_column(String(64), default="")
    public_key: Mapped[str] = mapped_column(Text, default="")
    private_enc: Mapped[str] = mapped_column(Text, default="")
    psk_enc: Mapped[str] = mapped_column(Text, default="")
    vless_uuid: Mapped[str] = mapped_column(String(36), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    rx_bytes: Mapped[int] = mapped_column(Integer, default=0)
    tx_bytes: Mapped[int] = mapped_column(Integer, default=0)
    last_seen: Mapped[str] = mapped_column(String(40), default="")
    traffic_json: Mapped[str] = mapped_column(Text, default="[]")
    counters_json: Mapped[str] = mapped_column(Text, default="{}")
    sub_token: Mapped[str] = mapped_column(String(64), default="")
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    server: Mapped[Server | None] = relationship(back_populates="clients")


class RoutingRule(Base):
    __tablename__ = "routing_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(120))
    match_json: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(16))
    exit_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    chain_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    group_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    hits: Mapped[int] = mapped_column(Integer, default=0)


class ExitGroup(Base):
    __tablename__ = "exit_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))


class ExitGroupMember(Base):
    __tablename__ = "exit_group_members"
    __table_args__ = (UniqueConstraint("group_id", "server_id", name="uq_group_server"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(36), index=True)
    server_id: Mapped[str] = mapped_column(String(36), index=True)
    weight: Mapped[int] = mapped_column(Integer, default=1)
    standby: Mapped[bool] = mapped_column(Boolean, default=False)


class Chain(Base):
    __tablename__ = "chains"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    entry_id: Mapped[str] = mapped_column(String(36), index=True)
    exit_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    group_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ChainTransit(Base):
    __tablename__ = "chain_transits"
    __table_args__ = (UniqueConstraint("chain_id", "server_id", name="uq_chain_transit"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chain_id: Mapped[str] = mapped_column(String(36), index=True)
    server_id: Mapped[str] = mapped_column(String(36), index=True)
    position: Mapped[int] = mapped_column(Integer, default=1)


class Balance(Base):
    __tablename__ = "balance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy: Mapped[str] = mapped_column(String(32), default="least_ping")
    auto_failover: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_sec: Mapped[int] = mapped_column(Integer, default=30)
    weights_json: Mapped[str] = mapped_column(Text, default="{}")


class AddressEntry(Base):
    __tablename__ = "address_entries"
    __table_args__ = (UniqueConstraint("kind", "value", "source", name="uq_address_source"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    value: Mapped[str] = mapped_column(String(255), index=True)
    action: Mapped[str] = mapped_column(String(16), default="cascade")
    source: Mapped[str] = mapped_column(String(80))
    source_date: Mapped[str] = mapped_column(String(40), default="")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)


class ListSource(Base):
    __tablename__ = "list_sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(16), default="cascade")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    selector: Mapped[str] = mapped_column(String(64), default="")
    last_fetch: Mapped[str] = mapped_column(String(40), default="")
    last_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    server_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="running")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lines: Mapped[list["JobLine"]] = relationship(back_populates="job", order_by="JobLine.id")


class JobLine(Base):
    __tablename__ = "job_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    step: Mapped[str] = mapped_column(String(64), default="")
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship(back_populates="lines")


class MetricSample(Base):
    __tablename__ = "metric_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[int] = mapped_column(Integer, index=True)
    server_id: Mapped[str] = mapped_column(String(36), index=True)
    cpu: Mapped[float | None] = mapped_column(Float, nullable=True)
    mem_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    disk_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_rx: Mapped[int] = mapped_column(Integer, default=0)
    net_tx: Mapped[int] = mapped_column(Integer, default=0)
    peers_online: Mapped[int] = mapped_column(Integer, default=0)
    ssh_ok: Mapped[bool] = mapped_column(Boolean, default=True)


class DnsHit(Base):
    __tablename__ = "dns_hits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[int] = mapped_column(Integer, index=True)
    server_id: Mapped[str] = mapped_column(String(36), index=True)
    client_name: Mapped[str] = mapped_column(String(80), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    domain: Mapped[str] = mapped_column(String(255), default="")
    site: Mapped[str] = mapped_column(String(255), default="")
    qtype: Mapped[str] = mapped_column(String(16), default="")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
