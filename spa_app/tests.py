from datetime import datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone

from .models import (
    EmployeeProfile,
    ExceptionType,
    ScheduleException,
    Service,
    ServiceCategory,
    User,
    UserRole,
)
from .services.booking_service import get_availability_details


class BookingAvailabilityTests(TestCase):
    def setUp(self):
        target_date = timezone.localdate() + timedelta(days=14)
        self.target_date = target_date + timedelta(
            days=(4 - target_date.weekday()) % 7
        )

        user = User.objects.create_user(
            username="+79990000001",
            password="password123",
            first_name="Анна",
            last_name="Прокофьева",
            email="anna@example.com",
            phone_number="+79990000001",
            role=UserRole.EMPLOYEE,
        )
        self.employee = EmployeeProfile.objects.create(
            user=user,
            position="Мастер",
            experience_years=5,
            work_days=[self.target_date.weekday()],
            work_start_time=time(8, 0),
            work_end_time=time(18, 0),
        )
        category = ServiceCategory.objects.create(name="SPA")
        self.service = Service.objects.create(
            name="Массаж",
            category=category,
            price=3000,
            duration_minutes=60,
        )
        self.service.employees.add(self.employee)

    def _aware(self, selected_date, selected_time):
        return timezone.make_aware(
            datetime.combine(selected_date, selected_time),
            timezone.get_current_timezone(),
        )

    def test_exception_on_another_day_does_not_hide_slots(self):
        another_date = self.target_date + timedelta(days=1)
        ScheduleException.objects.create(
            employee=self.employee,
            exception_type=ExceptionType.VACATION,
            start_datetime=self._aware(another_date, time(8, 0)),
            end_datetime=self._aware(another_date, time(18, 0)),
        )

        availability = get_availability_details(
            self.employee,
            self.service,
            self.target_date,
        )

        self.assertTrue(availability.slots)
        self.assertEqual(availability.reason, "ok")

    def test_exception_on_selected_day_blocks_slots(self):
        ScheduleException.objects.create(
            employee=self.employee,
            exception_type=ExceptionType.VACATION,
            start_datetime=self._aware(self.target_date, time(8, 0)),
            end_datetime=self._aware(self.target_date, time(18, 0)),
        )

        availability = get_availability_details(
            self.employee,
            self.service,
            self.target_date,
        )

        self.assertFalse(availability.slots)
        self.assertEqual(availability.reason, "blocked_by_exception")

    def test_non_working_day_returns_clear_reason(self):
        non_working_date = self.target_date + timedelta(days=1)

        availability = get_availability_details(
            self.employee,
            self.service,
            non_working_date,
        )

        self.assertFalse(availability.slots)
        self.assertEqual(availability.reason, "not_working_day")
