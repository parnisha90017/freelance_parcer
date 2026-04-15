from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import config


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    link: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


engine = create_async_engine(config.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def is_duplicate(link: str) -> bool:
    async with async_session() as session:
        statement = select(Project.id).where(Project.link == link).limit(1)
        result = await session.execute(statement)
        return result.scalar_one_or_none() is not None


async def save_project(link: str) -> None:
    async with async_session() as session:
        session.add(Project(link=link))
        await session.commit()


async def get_project_stats(platform_labels: dict[str, str]) -> dict[str, int | dict[str, int]]:
    now = datetime.utcnow()
    day_ago = now - timedelta(hours=24)
    today_start = datetime(now.year, now.month, now.day)

    async with async_session() as session:
        total_result = await session.execute(select(func.count(Project.id)))
        total_orders = int(total_result.scalar() or 0)

        today_result = await session.execute(
            select(func.count(Project.id)).where(Project.created_at >= today_start)
        )
        today_orders = int(today_result.scalar() or 0)

        last_24h_result = await session.execute(
            select(func.count(Project.id)).where(Project.created_at >= day_ago)
        )
        last_24h_orders = int(last_24h_result.scalar() or 0)

        links_result = await session.execute(select(Project.link, Project.created_at))
        platform_counts = {label: 0 for label in platform_labels.values()}
        platform_counts["Другое"] = 0
        platform_today_counts = {label: 0 for label in platform_labels.values()}
        platform_today_counts["Другое"] = 0

        for raw_link, created_at in links_result.all():
            link = str(raw_link or "").strip()
            host = urlparse(link).netloc.lower()
            if host.startswith("www."):
                host = host[4:]

            matched_label = None
            for domain, label in platform_labels.items():
                if host == domain or host.endswith(f".{domain}"):
                    matched_label = label
                    break

            bucket = matched_label or "Другое"
            platform_counts[bucket] += 1
            if created_at >= today_start:
                platform_today_counts[bucket] += 1

    return {
        "total_orders": total_orders,
        "today_orders": today_orders,
        "last_24h_orders": last_24h_orders,
        "platform_counts": platform_counts,
        "platform_today_counts": platform_today_counts,
    }
