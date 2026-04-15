from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select

from config import config
from database.db import Subscription, async_session

PAYMENT_LABELS = {
    "trial": "Триал",
    "stars": "Stars",
    "yookassa": "ЮКасса",
    "yoomoney": "ЮMoney",
    "manual": "Ручная активация",
    "owner": "Владелец",
}


@dataclass(slots=True)
class SubscriptionManager:
    async def get_subscription(self, user_id: int) -> Subscription | None:
        async with async_session() as session:
            statement = select(Subscription).where(Subscription.user_id == user_id).limit(1)
            result = await session.execute(statement)
            return result.scalar_one_or_none()

    async def is_subscribed(self, user_id: int) -> bool:
        if user_id == config.OWNER_ID:
            return True

        subscription = await self.get_subscription(user_id)
        if subscription is None or not subscription.is_active or subscription.expires_at is None:
            return False

        now = datetime.utcnow()
        return subscription.expires_at >= now

    async def create_trial(self, user_id: int, username: str | None) -> Subscription | None:
        existing = await self.get_subscription(user_id)
        if existing is not None:
            return None

        now = datetime.utcnow()
        async with async_session() as session:
            subscription = Subscription(
                user_id=user_id,
                username=self._normalize_username(username),
                payment_method="trial",
                paid_at=now,
                expires_at=now + timedelta(days=config.TRIAL_DAYS),
                is_active=True,
                notes="Автоматически создан триал",
            )
            session.add(subscription)
            await session.commit()
            await session.refresh(subscription)
            return subscription

    async def activate_subscription(
        self,
        user_id: int,
        username: str | None,
        method: str,
        days: int | None = None,
        notes: str | None = None,
    ) -> Subscription:
        duration_days = days or config.SUBSCRIPTION_DAYS
        now = datetime.utcnow()

        async with async_session() as session:
            statement = select(Subscription).where(Subscription.user_id == user_id).limit(1)
            result = await session.execute(statement)
            subscription = result.scalar_one_or_none()

            expires_base = now
            if subscription is not None and subscription.is_active and subscription.expires_at is not None:
                expires_base = max(subscription.expires_at, now)

            expires_at = expires_base + timedelta(days=duration_days)
            normalized_username = self._normalize_username(username)

            if subscription is None:
                subscription = Subscription(
                    user_id=user_id,
                    username=normalized_username,
                    payment_method=method,
                    paid_at=now,
                    expires_at=expires_at,
                    is_active=True,
                    notes=notes,
                )
                session.add(subscription)
            else:
                subscription.username = normalized_username or subscription.username
                subscription.payment_method = method
                subscription.paid_at = now
                subscription.expires_at = expires_at
                subscription.is_active = True
                if notes:
                    subscription.notes = notes

            await session.commit()
            await session.refresh(subscription)
            return subscription

    async def check_and_deactivate_expired(self) -> int:
        now = datetime.utcnow()
        async with async_session() as session:
            statement = select(Subscription).where(
                Subscription.is_active.is_(True),
                Subscription.expires_at.is_not(None),
                Subscription.expires_at < now,
            )
            result = await session.execute(statement)
            subscriptions = result.scalars().all()

            for subscription in subscriptions:
                subscription.is_active = False

            await session.commit()
            return len(subscriptions)

    async def get_status_text(self, user_id: int) -> str:
        if user_id == config.OWNER_ID:
            return "👑 Владелец — безлимитный доступ"

        subscription = await self.get_subscription(user_id)
        if subscription is None:
            return (
                "❌ Подписка не активна\n"
                f"Попробуй бесплатно {config.TRIAL_DAYS} дня через меню «💎 Подписка» или команду /subscribe"
            )

        if subscription.expires_at is None:
            return (
                "❌ Подписка не активна\n"
                "Оформи доступ через меню «💎 Подписка» или команду /subscribe"
            )

        now = datetime.utcnow()
        expires_at = subscription.expires_at
        formatted_date = expires_at.strftime("%d.%m.%Y")

        if not subscription.is_active or expires_at < now:
            return (
                f"⏰ Подписка истекла {formatted_date}\n"
                "Продлить можно через меню «💎 Подписка» или команду /subscribe"
            )

        days_left = self._days_left(expires_at, now)
        if subscription.payment_method == "trial":
            return (
                "🎁 Бесплатный триал\n"
                f"Активен до: {formatted_date}\n"
                f"Осталось: {days_left} {self._pluralize_days(days_left)}\n"
                "Оформить полную подписку: меню «💎 Подписка» или /subscribe"
            )

        payment_label = PAYMENT_LABELS.get(subscription.payment_method or "", subscription.payment_method or "Не указан")
        return (
            "✅ Подписка активна\n"
            f"Способ оплаты: {payment_label}\n"
            f"Активна до: {formatted_date}\n"
            f"Осталось: {days_left} {self._pluralize_days(days_left)}"
        )

    async def get_active_subscriber_ids(self) -> list[int]:
        now = datetime.utcnow()
        async with async_session() as session:
            statement = select(Subscription.user_id).where(
                Subscription.is_active.is_(True),
                Subscription.expires_at.is_not(None),
                Subscription.expires_at >= now,
            )
            result = await session.execute(statement)
            return [int(user_id) for user_id in result.scalars().all()]

    async def get_active_subscriber_count(self) -> int:
        return len(await self.get_active_subscriber_ids())

    async def get_stats(self) -> dict[str, int]:
        now = datetime.utcnow()
        async with async_session() as session:
            total_result = await session.execute(select(func.count(Subscription.id)))
            total = int(total_result.scalar() or 0)

            active_result = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.is_active.is_(True),
                    Subscription.expires_at.is_not(None),
                    Subscription.expires_at >= now,
                )
            )
            active = int(active_result.scalar() or 0)

            trial_result = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.is_active.is_(True),
                    Subscription.payment_method == "trial",
                    Subscription.expires_at.is_not(None),
                    Subscription.expires_at >= now,
                )
            )
            trials = int(trial_result.scalar() or 0)

            paid_result = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.is_active.is_(True),
                    Subscription.payment_method.is_not(None),
                    Subscription.payment_method != "trial",
                    Subscription.expires_at.is_not(None),
                    Subscription.expires_at >= now,
                )
            )
            paid = int(paid_result.scalar() or 0)

            expired_result = await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.expires_at.is_not(None),
                    Subscription.expires_at < now,
                )
            )
            expired = int(expired_result.scalar() or 0)

        return {
            "total": total,
            "active": active,
            "trials": trials,
            "paid": paid,
            "expired": expired,
        }

    async def get_expiring_soon_subscriptions(self, days: int = 3) -> list[Subscription]:
        now = datetime.utcnow()
        start = now + timedelta(days=days)
        end = start + timedelta(days=1)
        async with async_session() as session:
            statement = select(Subscription).where(
                Subscription.is_active.is_(True),
                Subscription.payment_method != "trial",
                Subscription.expires_at.is_not(None),
                Subscription.expires_at >= start,
                Subscription.expires_at < end,
            )
            result = await session.execute(statement)
            return list(result.scalars().all())

    async def get_users_summary(self) -> str:
        async with async_session() as session:
            statement = select(Subscription).order_by(
                Subscription.is_active.desc(),
                Subscription.expires_at.is_(None),
                Subscription.expires_at.desc(),
                Subscription.id.asc(),
            )
            result = await session.execute(statement)
            subscriptions = list(result.scalars().all())

        if not subscriptions:
            return "👥 Подписчики:\n\nСписок пуст."

        now = datetime.utcnow()
        lines: list[str] = []
        active_count = 0
        trial_count = 0

        for index, subscription in enumerate(subscriptions, start=1):
            username = self._display_username(subscription.username, subscription.user_id)
            expires_at = subscription.expires_at
            date_text = expires_at.strftime("%d.%m") if expires_at else "—"

            if subscription.payment_method == "trial" and subscription.is_active and expires_at and expires_at >= now:
                trial_count += 1
                active_count += 1
                line = f"{index}. {username} — триал до {date_text}"
            elif subscription.is_active and expires_at and expires_at >= now:
                active_count += 1
                method_label = PAYMENT_LABELS.get(subscription.payment_method or "", subscription.payment_method or "Не указан")
                line = f"{index}. {username} — активна до {date_text} ({method_label})"
            else:
                line = f"{index}. {username} — истекла {date_text}"

            lines.append(line)

        return (
            "👥 Подписчики:\n\n"
            + "\n".join(lines)
            + f"\n\nВсего: {len(subscriptions)}\nАктивных: {active_count}\nТриалов: {trial_count}"
        )

    def _normalize_username(self, username: str | None) -> str | None:
        if not username:
            return None
        normalized = username.strip().lstrip("@")
        return normalized or None

    def _display_username(self, username: str | None, user_id: int) -> str:
        normalized = self._normalize_username(username)
        if normalized:
            return f"@{normalized}"
        return str(user_id)

    def _days_left(self, expires_at: datetime, now: datetime) -> int:
        delta = expires_at - now
        return max(1, delta.days + (1 if delta.seconds > 0 or delta.microseconds > 0 else 0))

    def _pluralize_days(self, days: int) -> str:
        last_two = days % 100
        last_one = days % 10
        if 11 <= last_two <= 14:
            return "дней"
        if last_one == 1:
            return "день"
        if 2 <= last_one <= 4:
            return "дня"
        return "дней"


subscription_manager = SubscriptionManager()
