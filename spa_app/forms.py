from django import forms
from django.db.models import Q
from django.utils import timezone

from .models import (
    EmployeeProfile,
    Review,
    ScheduleException,
    Service,
    ServiceCategory,
    User,
    UserRole,
    WeekDay,
)


def set_form_control(fields):
    for field in fields:
        field.widget.attrs.update({"class": "form-control"})


class AppointmentForm(forms.Form):
    last_name = forms.CharField(label="Фамилия", max_length=150)
    first_name = forms.CharField(label="Имя", max_length=150)
    middle_name = forms.CharField(label="Отчество", max_length=150, required=False)
    phone_number = forms.CharField(label="Телефон", max_length=20)
    email = forms.EmailField(label="Электронная почта")

    service = forms.ModelChoiceField(
        label="Услуга",
        queryset=Service.objects.filter(is_active=True, category__is_active=True),
        empty_label="Выберите услугу",
    )
    employee = forms.ModelChoiceField(
        label="Специалист",
        queryset=EmployeeProfile.objects.none(),
        empty_label="Выберите специалиста",
    )
    date = forms.DateField(
        label="Дата",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    time = forms.CharField(
        label="Время",
        widget=forms.Select(),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        self.uses_authenticated_account = bool(
            user
            and user.is_authenticated
            and user.role in [UserRole.CLIENT, UserRole.EMPLOYEE]
        )

        if args and self.uses_authenticated_account:
            data = args[0].copy()
            for field in [
                "last_name",
                "first_name",
                "middle_name",
                "phone_number",
                "email",
            ]:
                data[field] = getattr(user, field)
            args = (data,) + args[1:]

        super().__init__(*args, **kwargs)
        set_form_control(self.fields.values())

        self.fields["time"].widget.choices = [
            ("", "Сначала выберите услугу, специалиста и дату")
        ]

        service = self._get_service()
        if service:
            self.fields["employee"].queryset = (
                service.employees.select_related("user")
                .filter(is_active=True)
                .order_by("user__last_name", "user__first_name")
            )

        employee = self._get_employee(service)
        selected_date = self.data.get("date") or self.initial.get("date")

        if service and employee and selected_date:
            self._set_time_choices(service, employee, selected_date)

        if self.uses_authenticated_account:
            for field in [
                "last_name",
                "first_name",
                "middle_name",
                "phone_number",
                "email",
            ]:
                self.fields[field].required = False
                self.fields[field].widget = forms.HiddenInput()

    def _get_service(self):
        service_id = self.data.get("service") or self.initial.get("service")
        if not service_id:
            return None

        try:
            return Service.objects.get(
                pk=service_id,
                is_active=True,
                category__is_active=True,
            )
        except (Service.DoesNotExist, TypeError, ValueError):
            return None

    def _get_employee(self, service):
        employee_id = self.data.get("employee") or self.initial.get("employee")
        if not service or not employee_id:
            return None

        try:
            return self.fields["employee"].queryset.get(pk=employee_id)
        except (EmployeeProfile.DoesNotExist, TypeError, ValueError):
            return None

    def _set_time_choices(self, service, employee, selected_date):
        try:
            if isinstance(selected_date, str):
                selected_date = forms.DateField().clean(selected_date)

            from .services.booking_service import get_availability_details

            availability = get_availability_details(employee, service, selected_date)
            choices = [
                (slot.strftime("%H:%M"), slot.strftime("%H:%M"))
                for slot in availability.slots
            ]

            self.fields["time"].widget.choices = choices or [
                ("", "Нет свободных слотов для записи")
            ]
        except forms.ValidationError:
            pass

    def clean_phone_number(self):
        phone = self.cleaned_data["phone_number"].strip()

        if not self.uses_authenticated_account:
            if User.objects.filter(phone_number=phone).exists():
                raise forms.ValidationError(
                    "Пользователь с таким телефоном уже существует. "
                    "Войдите в личный кабинет, чтобы создать запись."
                )

        return phone

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()

        if not self.uses_authenticated_account:
            if User.objects.filter(email__iexact=email).exists():
                raise forms.ValidationError(
                    "Пользователь с такой почтой уже существует. "
                    "Войдите в личный кабинет, чтобы создать запись."
                )

        return email

    def clean(self):
        cleaned_data = super().clean()

        if self.uses_authenticated_account:
            for field in [
                "last_name",
                "first_name",
                "middle_name",
                "phone_number",
                "email",
            ]:
                cleaned_data[field] = getattr(self.user, field)

        return cleaned_data


class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = [
            "name",
            "category",
            "description",
            "price",
            "duration_minutes",
            "employees",
            "is_active",
        ]
        labels = {
            "name": "Название",
            "category": "Категория",
            "description": "Описание",
            "price": "Цена",
            "duration_minutes": "Длительность (мин)",
            "employees": "Специалисты",
            "is_active": "Активна",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "employees": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        selected_ids = []
        if self.instance.pk:
            selected_ids = list(self.instance.employees.values_list("pk", flat=True))

        self.fields["employees"].queryset = (
            EmployeeProfile.objects.select_related("user")
            .filter(Q(is_active=True) | Q(pk__in=selected_ids))
            .order_by("user__last_name", "user__first_name")
            .distinct()
        )
        self.fields["category"].queryset = ServiceCategory.objects.order_by("name")

        set_form_control([
            self.fields["name"],
            self.fields["description"],
            self.fields["price"],
            self.fields["duration_minutes"],
        ])

        self.fields["category"].widget.attrs.update({"class": "form-select"})
        self.fields["employees"].widget.attrs.update({"class": "list-unstyled mb-0"})
        self.fields["is_active"].widget.attrs.update({"class": "form-check-input"})
        self.fields["price"].widget.attrs.update({"step": "0.01", "min": "0"})
        self.fields["duration_minutes"].widget.attrs.update({"min": "5"})
        self.fields["employees"].label_from_instance = (
            lambda employee: f"{employee.user} ({employee.position})"
        )


class ServiceCategoryForm(forms.ModelForm):
    class Meta:
        model = ServiceCategory
        fields = ["name", "description", "image", "is_active"]
        labels = {
            "name": "Название",
            "description": "Описание",
            "image": "Изображение",
            "is_active": "Активна",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_form_control([
            self.fields["name"],
            self.fields["description"],
            self.fields["image"],
        ])
        self.fields["is_active"].widget.attrs.update({"class": "form-check-input"})


class EmployeeCreateForm(forms.Form):
    username = forms.CharField(label="Логин", max_length=150)
    last_name = forms.CharField(label="Фамилия", max_length=150)
    first_name = forms.CharField(label="Имя", max_length=150)
    middle_name = forms.CharField(label="Отчество", max_length=150, required=False)
    phone_number = forms.CharField(label="Телефон", max_length=20)
    email = forms.EmailField(label="Email")
    position = forms.CharField(label="Должность", max_length=100)
    description = forms.CharField(
        label="Описание",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    experience_years = forms.IntegerField(label="Опыт (лет)", min_value=0)
    hire_date = forms.DateField(
        label="Дата приема на работу",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    work_days = forms.MultipleChoiceField(
        label="Рабочие дни",
        required=False,
        choices=WeekDay.choices,
        widget=forms.CheckboxSelectMultiple(),
    )
    work_start_time = forms.TimeField(
        label="Начало рабочего дня",
        required=False,
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    work_end_time = forms.TimeField(
        label="Конец рабочего дня",
        required=False,
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    photo = forms.ImageField(label="Фото", required=False)
    is_active = forms.BooleanField(label="Активен", required=False, initial=True)

    def __init__(self, *args, **kwargs):
        self.user_instance = kwargs.pop("user_instance", None)
        super().__init__(*args, **kwargs)

        set_form_control([
            field
            for name, field in self.fields.items()
            if name not in ["work_days", "is_active"]
        ])
        self.fields["work_days"].widget.attrs.update({"class": "list-unstyled mb-0"})
        self.fields["is_active"].widget.attrs.update({"class": "form-check-input"})

    def _user_queryset(self):
        users = User.objects.all()
        if self.user_instance:
            users = users.exclude(pk=self.user_instance.pk)
        return users

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if self._user_queryset().filter(username__iexact=username).exists():
            raise forms.ValidationError("Такой логин уже занят.")
        return username

    def clean_phone_number(self):
        phone = self.cleaned_data["phone_number"].strip()
        if self._user_queryset().filter(phone_number=phone).exists():
            raise forms.ValidationError("Пользователь с таким телефоном уже существует.")
        return phone

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if self._user_queryset().filter(email__iexact=email).exists():
            raise forms.ValidationError("Пользователь с таким email уже существует.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        work_days = cleaned_data.get("work_days") or []
        start = cleaned_data.get("work_start_time")
        end = cleaned_data.get("work_end_time")

        if bool(start) != bool(end):
            raise forms.ValidationError("Укажите и начало, и конец рабочего дня.")

        if start and end and start >= end:
            self.add_error("work_end_time", "Время окончания должно быть позже начала.")

        if work_days and not (start and end):
            raise forms.ValidationError("Если выбраны рабочие дни, укажите время работы.")

        if (start or end) and not work_days:
            raise forms.ValidationError("Если указано время работы, выберите рабочие дни.")

        cleaned_data["work_days"] = sorted({int(day) for day in work_days})
        return cleaned_data


class ScheduleExceptionForm(forms.ModelForm):
    class Meta:
        model = ScheduleException
        fields = ["employee", "exception_type", "start_datetime", "end_datetime", "reason"]
        labels = {
            "employee": "Сотрудник",
            "exception_type": "Тип отсутствия",
            "start_datetime": "Начало",
            "end_datetime": "Окончание",
            "reason": "Комментарий",
        }
        widgets = {
            "start_datetime": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "end_datetime": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "reason": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        selected_ids = []
        if self.instance.pk:
            selected_ids = [self.instance.employee_id]

        self.fields["employee"].queryset = (
            EmployeeProfile.objects.select_related("user")
            .filter(Q(is_active=True) | Q(pk__in=selected_ids))
            .order_by("user__last_name", "user__first_name")
            .distinct()
        )
        self.fields["employee"].label_from_instance = (
            lambda employee: f"{employee.user} ({employee.position})"
        )

        self.fields["employee"].widget.attrs.update({"class": "form-select"})
        self.fields["exception_type"].widget.attrs.update({"class": "form-select"})
        set_form_control([
            self.fields["start_datetime"],
            self.fields["end_datetime"],
            self.fields["reason"],
        ])

        self.fields["start_datetime"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["end_datetime"].input_formats = ["%Y-%m-%dT%H:%M"]

        if self.instance.pk:
            self.initial["start_datetime"] = timezone.localtime(
                self.instance.start_datetime
            ).strftime("%Y-%m-%dT%H:%M")
            self.initial["end_datetime"] = timezone.localtime(
                self.instance.end_datetime
            ).strftime("%Y-%m-%dT%H:%M")

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get("start_datetime")
        end = cleaned_data.get("end_datetime")

        if start and timezone.is_naive(start):
            cleaned_data["start_datetime"] = timezone.make_aware(start)

        if end and timezone.is_naive(end):
            cleaned_data["end_datetime"] = timezone.make_aware(end)

        if start and end and start >= end:
            self.add_error("end_datetime", "Окончание должно быть позже начала.")

        return cleaned_data


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ["rating", "text"]
        labels = {
            "rating": "Оценка",
            "text": "Отзыв",
        }
        widgets = {
            "rating": forms.Select(
                choices=[
                    (5, "5 - отлично"),
                    (4, "4 - хорошо"),
                    (3, "3 - нормально"),
                    (2, "2 - плохо"),
                    (1, "1 - очень плохо"),
                ]
            ),
            "text": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rating"].widget.attrs.update({"class": "form-select"})
        self.fields["text"].widget.attrs.update({"class": "form-control"})


class LoginForm(forms.Form):
    identifier = forms.CharField(label="Телефон или логин", max_length=150)
    password = forms.CharField(label="Пароль", widget=forms.PasswordInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_form_control(self.fields.values())


class RegistrationForm(forms.Form):
    last_name = forms.CharField(label="Фамилия", max_length=150)
    first_name = forms.CharField(label="Имя", max_length=150)
    middle_name = forms.CharField(label="Отчество", max_length=150, required=False)
    phone_number = forms.CharField(label="Телефон", max_length=20)
    email = forms.EmailField(label="Email")
    password1 = forms.CharField(label="Пароль", widget=forms.PasswordInput())
    password2 = forms.CharField(label="Повторите пароль", widget=forms.PasswordInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_form_control(self.fields.values())

    def clean_phone_number(self):
        phone = self.cleaned_data["phone_number"].strip()
        if User.objects.filter(phone_number=phone).exists():
            raise forms.ValidationError("Пользователь с таким телефоном уже существует.")
        return phone

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Пользователь с таким email уже существует.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")

        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Пароли не совпадают.")

        if password1 and len(password1) < 8:
            self.add_error("password1", "Пароль должен содержать минимум 8 символов.")

        return cleaned_data