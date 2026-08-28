"""Resetting a user's onboarding actually sends them back to their first run.

`OnboardingService.reset` used to DELETE the onboarding row, which achieved the
opposite of its stated purpose: a missing row means "existing user,
grandfathered" and is reported as `wizard_completed=True`. Resetting therefore
marked the user permanently complete and the wizard could never reappear.
"""

from sqlalchemy import select

from src.db.models import User, UserOnboarding
from src.services.onboarding_service import OnboardingService


async def _user(db_session, username: str = "member") -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash="x",
        is_superadmin=False,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


class TestOnboardingReset:
    async def test_reset_clears_the_flags_instead_of_deleting_the_row(self, db_session):
        user = await _user(db_session)
        svc = OnboardingService(db_session)

        row = await svc.get_or_create(user)
        row.wizard_completed = True
        row.tour_completed = True
        row.wizard_current_step = 6
        row.tour_current_step = 4
        row.tour_variant = "developer"
        row.tour_seen_steps = ["chat.input", "chat.modes"]
        await db_session.commit()

        result = await svc.reset(user)

        # The row must survive: deleting it is read as "grandfathered complete".
        assert result is not None
        assert result.wizard_completed is False
        assert result.tour_completed is False
        assert result.wizard_current_step == 0
        assert result.tour_current_step == 0
        # Walkthrough history cleared too, or the tour resumes mid-way through a
        # run the user is supposed to be starting over.
        assert result.tour_variant is None
        assert result.tour_seen_steps is None

        rows = (
            await db_session.execute(
                select(UserOnboarding).where(UserOnboarding.user_id == user.id)
            )
        ).scalars().all()
        assert len(rows) == 1

    async def test_reset_creates_a_row_for_a_grandfathered_user(self, db_session):
        """A user with no row must become resettable, not stay untouchable."""
        user = await _user(db_session, "legacy")
        svc = OnboardingService(db_session)

        assert await svc.get_or_create(user, create=False) is None

        result = await svc.reset(user)

        assert result.wizard_completed is False
        assert result.tour_completed is False
        assert await svc.get_or_create(user, create=False) is not None

    async def test_reset_is_idempotent(self, db_session):
        user = await _user(db_session, "twice")
        svc = OnboardingService(db_session)

        await svc.reset(user)
        result = await svc.reset(user)

        assert result.wizard_completed is False
        rows = (
            await db_session.execute(
                select(UserOnboarding).where(UserOnboarding.user_id == user.id)
            )
        ).scalars().all()
        assert len(rows) == 1

    async def test_completing_after_a_reset_still_works(self, db_session):
        """The reset row must be a normal row, not a special-cased one."""
        user = await _user(db_session, "recompletes")
        svc = OnboardingService(db_session)

        await svc.reset(user)
        row = await svc.complete_wizard(user, has_source=False)

        assert row.wizard_completed is True
        assert row.setup_status == "skipped"
