from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class DailyReport(Base):
    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="published", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[list["DailyReportItem"]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="DailyReportItem.position",
    )

    __table_args__ = (
        CheckConstraint("status IN ('published', 'withdrawn')", name="ck_daily_reports_status"),
    )


class DailyReportItem(Base):
    __tablename__ = "daily_report_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("daily_reports.id", ondelete="CASCADE"), index=True
    )
    normalized_item_id: Mapped[int] = mapped_column(
        ForeignKey("normalized_items.id", ondelete="RESTRICT"), index=True
    )
    section: Mapped[str] = mapped_column(String(20))
    position: Mapped[int] = mapped_column(Integer)

    report: Mapped[DailyReport] = relationship(back_populates="items")

    __table_args__ = (
        UniqueConstraint("report_id", "normalized_item_id", name="uq_daily_report_item_message"),
        UniqueConstraint("report_id", "section", "position", name="uq_daily_report_item_position"),
        CheckConstraint(
            "section IN ('lolpc', 'esports', 'tft', 'other')",
            name="ck_daily_report_items_section",
        ),
        CheckConstraint("position >= 1", name="ck_daily_report_items_position"),
    )
