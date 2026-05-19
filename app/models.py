from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    totp_secret: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(20), default="user")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tasks: Mapped[list["Task"]] = relationship(back_populates="assignee")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(160), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String(20), default="chore")
    status: Mapped[str] = mapped_column(String(20), default="incomplete", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=2)
    color: Mapped[str] = mapped_column(String(20), default="#147d74")
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    project_name: Mapped[str] = mapped_column(String(120), default="", index=True)
    project_order: Mapped[int] = mapped_column(Integer, default=0)
    reset_frequency: Mapped[str] = mapped_column(String(30), default="daily")
    reset_value: Mapped[str] = mapped_column(String(100), default="")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    next_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    assignee: Mapped[User | None] = relationship(back_populates="tasks")
    history: Mapped[list["TaskHistory"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class TaskHistory(Base):
    __tablename__ = "task_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    completed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[Task] = relationship(back_populates="history")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
