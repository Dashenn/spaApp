from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.utils import timezone

from ..models import Appointment, AppointmentStatus, ScheduleException, WeekDay


@dataclass
class AvailabilityResult:
    slots: list
    message: str = ""
    reason: str = "ok"


def _make_aware(selected_date, selected_time):
    value = datetime.combine(selected_date, selected_time)
    return timezone.make_aware(value, timezone.get_current_timezone())


def _format_work_days(work_days):
    labels = dict(WeekDay.choices)
    return ", ".join(labels.get(int(day), str(day)) for day in sorted(work_days))


def _get_work_interval(employee, selected_date):
    if not employee.work_days or not employee.work_start_time or not employee.work_end_time:
        return None

    work_days = {int(day) for day in employee.work_days}

    if selected_date.weekday() not in work_days:
        return None

    return (
        _make_aware(selected_date, employee.work_start_time),
        _make_aware(selected_date, employee.work_end_time),
    )


def _subtract_intervals(free_intervals, busy_intervals):
    free = free_intervals[:]

    for busy_start, busy_end in busy_intervals:
        result = []

        for free_start, free_end in free:
            if busy_end <= free_start or busy_start >= free_end:
                result.append((free_start, free_end))
            else:
                if busy_start > free_start:
                    result.append((free_start, busy_start))
                if busy_end < free_end:
                    result.append((busy_end, free_end))

        free = result

    return free


def _get_no_schedule_result(employee, selected_date):
    if not employee.work_days or not employee.work_start_time or not employee.work_end_time:
        return AvailabilityResult(
            [],
            "У специалиста пока не настроен рабочий график.",
            "no_schedule",
        )

    return AvailabilityResult(
        [],
        f"Специалист не работает в этот день. Рабочие дни: {_format_work_days(employee.work_days)}.",
        "not_working_day",
    )


def get_availability_details(employee, service, selected_date, step_minutes=30):
    work_interval = _get_work_interval(employee, selected_date)

    if not work_interval:
        return _get_no_schedule_result(employee, selected_date)

    duration = timedelta(minutes=service.duration_minutes)
    work_start, work_end = work_interval

    if work_end - work_start < duration:
        return AvailabilityResult(
            [],
            "Длительность услуги больше рабочего времени специалиста в этот день.",
            "service_too_long",
        )

    day_start = _make_aware(selected_date, time.min)
    day_end = _make_aware(selected_date, time.max)

    exceptions = list(
        ScheduleException.objects.filter(
            employee=employee,
            start_datetime__lt=day_end,
            end_datetime__gt=day_start,
        ).values_list("start_datetime", "end_datetime")
    )

    appointments = list(
        Appointment.objects.filter(
            employee=employee,
            status__in=[AppointmentStatus.CREATED, AppointmentStatus.CONFIRMED],
            start_datetime__lt=day_end,
            end_datetime__gt=day_start,
        ).values_list("start_datetime", "end_datetime")
    )

    free_intervals = _subtract_intervals(
        [work_interval],
        exceptions + appointments,
    )

    slots = []
    step = timedelta(minutes=step_minutes)
    now = timezone.now()

    for free_start, free_end in free_intervals:
        current = free_start

        while current + duration <= free_end:
            if current > now:
                slots.append(current)

            current += step

    if slots:
        return AvailabilityResult(slots)

    return AvailabilityResult(
        [],
        "На выбранную дату нет доступного времени.",
        "no_slots",
    )


def get_available_slots(employee, service, selected_date, step_minutes=30):
    return get_availability_details(
        employee,
        service,
        selected_date,
        step_minutes=step_minutes,
    ).slots