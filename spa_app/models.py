from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class UserRole(models.TextChoices):
    ADMIN = "admin", "Администратор"
    EMPLOYEE = "employee", "Сотрудник"
    CLIENT = "client", "Клиент"


class AppointmentStatus(models.TextChoices):
    CREATED = "created", "Создана"
    CONFIRMED = "confirmed", "Подтверждена"
    COMPLETED = "completed", "Завершена"
    FAILED = "failed", "Завершена неуспешно"
    CANCELLED = "cancelled", "Отменена"

class PaymentMethod(models.TextChoices):
    CASH = "cash", "Наличные"
    CARD = "card", "Карта"


class ExceptionType(models.TextChoices):
    BREAK = "break", "Перерыв"
    VACATION = "vacation", "Отпуск"
    SICK_LEAVE = "sick_leave", "Больничный"
    DAY_OFF = "day_off", "Выходной"
    BLOCKED = "blocked", "Блокировка времени"


class WeekDay(models.IntegerChoices):
    MONDAY = 0, "Понедельник"
    TUESDAY = 1, "Вторник"
    WEDNESDAY = 2, "Среда"
    THURSDAY = 3, "Четверг"
    FRIDAY = 4, "Пятница"
    SATURDAY = 5, "Суббота"
    SUNDAY = 6, "Воскресенье"


class User(AbstractUser):
    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    middle_name = models.CharField(max_length=150, blank=True)
    role = models.CharField(
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.CLIENT,
    )
    phone_number = models.CharField(max_length=20, unique=True)

    def __str__(self):
        full_name = f"{self.last_name} {self.first_name} {self.middle_name}".strip()
        return full_name or self.phone_number


class ClientProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="client_profile",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.user)


class EmployeeProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    position = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    experience_years = models.PositiveIntegerField()
    photo = models.ImageField(upload_to="employees/", null=True, blank=True)
    is_active = models.BooleanField(default=True)
    hire_date = models.DateField(null=True, blank=True)

    work_start_time = models.TimeField(null=True, blank=True)
    work_end_time = models.TimeField(null=True, blank=True)
    work_days = models.JSONField(default=list, blank=True)

    def __str__(self):
        return str(self.user)

    def clean(self):
        super().clean()

        if bool(self.work_start_time) != bool(self.work_end_time):
            raise ValidationError("Укажите и начало, и конец рабочего дня.")

        if self.work_start_time and self.work_end_time:
            if self.work_start_time >= self.work_end_time:
                raise ValidationError(
                    "Начало рабочего дня должно быть раньше окончания."
                )

        if not isinstance(self.work_days, list):
            raise ValidationError("Рабочие дни должны быть списком.")

        days = []

        for day in self.work_days:
            try:
                day = int(day)
            except (TypeError, ValueError):
                raise ValidationError("Некорректный день недели.")

            if day < WeekDay.MONDAY or day > WeekDay.SUNDAY:
                raise ValidationError("Некорректный день недели.")

            days.append(day)

        if days and not (self.work_start_time and self.work_end_time):
            raise ValidationError("Если выбраны рабочие дни, укажите время работы.")

        self.work_days = sorted(set(days))

    def get_work_days_display(self):
        if not self.work_days:
            return "Не настроено"

        labels = dict(WeekDay.choices)
        return ", ".join(labels.get(int(day), str(day)) for day in self.work_days)

    def get_work_time_display(self):
        if not self.work_start_time or not self.work_end_time:
            return "Не настроено"

        return f"{self.work_start_time:%H:%M} - {self.work_end_time:%H:%M}"

    def get_work_schedule_display(self):
        if not self.work_days or not self.work_start_time or not self.work_end_time:
            return "Не настроено"

        return f"{self.get_work_days_display()} | {self.get_work_time_display()}"


class ServiceCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    image = models.ImageField(
        upload_to="service_categories/",
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Service(models.Model):
    name = models.CharField(max_length=150)
    category = models.ForeignKey(
        ServiceCategory,
        on_delete=models.PROTECT,
        related_name="services",
    )
    description = models.TextField(blank=True)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    duration_minutes = models.PositiveIntegerField(
        validators=[MinValueValidator(5)],
    )
    employees = models.ManyToManyField(
        EmployeeProfile,
        related_name="services",
        blank=True,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "category"],
                name="unique_service_name_per_category",
            )
        ]

    def __str__(self):
        return self.name


class ScheduleException(models.Model):
    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.CASCADE,
        related_name="schedule_exceptions",
    )
    exception_type = models.CharField(
        max_length=20,
        choices=ExceptionType.choices,
    )
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["employee", "start_datetime"]

    def __str__(self):
        return f"{self.employee} | {self.get_exception_type_display()}"

    def clean(self):
        if self.start_datetime >= self.end_datetime:
            raise ValidationError("Начало отсутствия должно быть раньше окончания.")


class Appointment(models.Model):
    client = models.ForeignKey(
        ClientProfile,
        on_delete=models.CASCADE,
        related_name="appointments",
    )
    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.PROTECT,
        related_name="appointments",
    )
    service = models.ForeignKey(
        Service,
        on_delete=models.PROTECT,
        related_name="appointments",
    )
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()
    status = models.CharField(
        max_length=20,
        choices=AppointmentStatus.choices,
        default=AppointmentStatus.CREATED,
    )
    comment = models.TextField(blank=True)
    is_paid = models.BooleanField(default=False)
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        blank=True,
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    

    is_refunded = models.BooleanField(default=False)
    refunded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_datetime"]

    def __str__(self):
        return f"{self.client} | {self.service} | {self.start_datetime}"

    def clean(self):
        if self.start_datetime >= self.end_datetime:
            raise ValidationError("Время начала записи должно быть раньше окончания.")

        if self.start_datetime < timezone.now():
            raise ValidationError("Нельзя создать запись на прошедшее время.")

        if not self.employee.is_active:
            raise ValidationError("Нельзя записать клиента к неактивному сотруднику.")

        if not self.service.is_active:
            raise ValidationError("Нельзя записать клиента на неактивную услугу.")

        if not self.service.employees.filter(pk=self.employee.pk).exists():
            raise ValidationError("Выбранный сотрудник не оказывает данную услугу.")

        expected_duration = timedelta(minutes=self.service.duration_minutes)
        actual_duration = self.end_datetime - self.start_datetime

        if actual_duration != expected_duration:
            raise ValidationError("Время записи не соответствует длительности услуги.")

        overlapping = Appointment.objects.filter(
            employee=self.employee,
            status__in=[
                AppointmentStatus.CREATED,
                AppointmentStatus.CONFIRMED,
            ],
            start_datetime__lt=self.end_datetime,
            end_datetime__gt=self.start_datetime,
        ).exclude(pk=self.pk)

        if overlapping.exists():
            raise ValidationError("У сотрудника уже есть запись на это время.")


class Review(models.Model):
    appointment = models.OneToOneField(
        Appointment,
        on_delete=models.CASCADE,
        related_name="review",
        null=True,
        blank=True,
    )
    client = models.ForeignKey(
        ClientProfile,
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    service = models.ForeignKey(
        Service,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviews",
    )
    text = models.TextField()
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_rejected = models.BooleanField(default=False)
    is_published = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        super().clean()

        if self.appointment_id and self.client_id:
            if self.appointment.client_id != self.client_id:
                raise ValidationError(
                    "Отзыв должен принадлежать клиенту выбранной записи."
                )

        if self.appointment_id and self.service_id:
            if self.appointment.service_id != self.service_id:
                raise ValidationError(
                    "Услуга в отзыве должна совпадать с услугой записи."
                )

    def __str__(self):
        return f"Отзыв {self.client} ({self.rating}/5)"
