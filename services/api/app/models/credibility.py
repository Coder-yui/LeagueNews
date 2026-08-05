from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SourceReliabilityHistory(Base):
    __tablename__ = "source_reliability_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        unique=True,
    )
    confirmed_count: Mapped[int] = mapped_column(Integer, default=0)
    refuted_count: Mapped[int] = mapped_column(Integer, default=0)
    alpha: Mapped[float] = mapped_column(Float)
    beta: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    source: Mapped["Source"] = relationship()  # noqa: F821

    __table_args__ = (
        CheckConstraint(
            "confirmed_count >= 0",
            name="ck_source_reliability_confirmed_count",
        ),
        CheckConstraint(
            "refuted_count >= 0",
            name="ck_source_reliability_refuted_count",
        ),
        CheckConstraint("alpha > 0", name="ck_source_reliability_alpha"),
        CheckConstraint("beta >= 0", name="ck_source_reliability_beta"),
    )
