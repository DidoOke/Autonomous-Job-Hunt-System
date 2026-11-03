from datetime import datetime
from typing import List, Optional
from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    desc,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    tier: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    hubs: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text), nullable=True)
    sector: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    clearance: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    sources: Mapped[List["Source"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    jobs: Mapped[List["Job"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    rule: Mapped[Optional["CompanyRule"]] = relationship(
        back_populates="company", cascade="all, delete-orphan", uselist=False
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    layer: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    interval_min: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="active", nullable=False)
    last_ok: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fail_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    baselined: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority: Mapped[int] = mapped_column(SmallInteger, default=2, nullable=False)

    company: Mapped["Company"] = relationship(back_populates="sources")
    sightings: Mapped[List["Sighting"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_sources_company_id_status", "company_id", "status"),
        Index("ix_sources_priority_status", "priority", "status"),
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    location_raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_uk: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_manchester: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_london: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    remote_ambiguous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    job_kind: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    role_family: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    apply_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    posted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    duplicate_of: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    inserted_cycle_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    score: Mapped[int] = mapped_column(Integer, server_default="0", default=0, nullable=False)
    tier: Mapped[str] = mapped_column(String(10), server_default="drop", default="drop", nullable=False)
    matched_rules: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="jobs")
    sightings: Mapped[List["Sighting"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    notifications: Mapped[List["Notification"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    application: Mapped[Optional["Application"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        Index("ix_jobs_first_seen_desc", desc("first_seen")),
        Index("ix_jobs_fingerprint", "fingerprint", unique=False),
        Index("ix_jobs_dedupe_key", "dedupe_key", unique=False),
        Index("ix_jobs_is_test", "is_test"),
        Index("ix_jobs_tier_score", "tier", desc("score")),
        Index("ix_jobs_inserted_cycle", "inserted_cycle_id"),
    )


class CompanyRule(Base):
    __tablename__ = "company_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    force_tech: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False, nullable=False)
    force_drop: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="rule")


class Sighting(Base):
    __tablename__ = "sightings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    job: Mapped["Job"] = relationship(back_populates="sightings")
    source: Mapped["Source"] = relationship(back_populates="sightings")

    __table_args__ = (
        Index(
            "ix_sightings_source_external",
            "source_id",
            "external_id",
            unique=True,
        ),
    )


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    job: Mapped["Job"] = relationship(back_populates="notifications")


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, default="applied", nullable=False)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    job: Mapped["Job"] = relationship(back_populates="application")
